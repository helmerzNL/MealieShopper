# Onderzoek: Crisp (online supermarkt)

Doel: vaststellen of MealieShopper Crisp kan koppelen naast Albert Heijn, Picnic en Jumbo, zodat het weekmenu ook in het
mandje bij Crisp terechtkomt. Dit is een spike van 1 november 2026. Gelezen zijn de openbare pagina's van `crisp.nl`, de
voorwaarden van Crisp, Picnic, Jumbo en Albert Heijn, en de broncode van een open-source Home Assistant-integratie voor
Crisp. Er is niet ingelogd, er is geen account gebruikt en er zijn geen verzoeken gedaan aan de app-API van Crisp. Er is
niets in de app gebouwd.

## Uitkomst: nog niet toegevoegd

**Technisch kan het waarschijnlijk wel, maar het kan nog niet meteen.**

- Crisp heeft geen officiële API en geen webshop. Bestellen gaat alleen via de Android- en iOS-app, die in React Native
  is gebouwd.
- Van de app-API zijn openbaar alleen het inloggen (met een code per mail) en de bestellingen bekend. Hoe je producten
  zoekt, wat een product kost, hoe groot de verpakking is en hoe je het mandje vult, beschrijft geen enkel openbaar
  project. Dat verschilt van Picnic en Jumbo: daar volgen we bestaande community-projecten (`python-picnic-api2` en
  `jumbo-mcp`).
- De voorwaarden van Crisp verbieden tools die informatie ophalen, net als die van Picnic en Jumbo, die we al gebruiken
  (zie [Voorwaarden](#voorwaarden-naast-die-van-de-winkels-die-er-al-zijn)). Crisp is daarin niet strenger dan die twee.
- De **Weekbox** van Crisp staat openbaar op `crisp.nl/weekbox`, met volledige recepten. Die zou als derde aanbieder in
  de tab **Maaltijdboxen** kunnen.

## Wat is vastgesteld

| Onderdeel | Bevinding |
| --- | --- |
| Bestellen | Alleen in de app. `crisp.nl/app` is een landingspagina met een QR-code ("Crisp werkt via onze Android en iOS app"). De sitemap heeft 10 marketingpagina's en geen productpagina's. Er zijn meer dan 3000 producten. Een bestelling is minimaal €40 en wordt de volgende dag bezorgd. Crisp zit in Nederland en België (`crisp.app`). |
| App-API | `https://crispapp.nl/v1/...`, bekend uit [NLthijs48/home-assistant-crisp](https://github.com/NLthijs48/home-assistant-crisp) (MIT, onderhouden, laatste commit 31 oktober 2026). Die stuurt `Origin: https://crispapp.nl`, `Authorization: bearer <client_id>`, `Content-Type: application/json`, `X-Crisp-Agent: crisp/app/android/667` en een eigen User-Agent mee. |
| Inloggen | De `client_id` maak je zelf: 20 tekens uit a-z, A-Z en 0-9, zoals de app ook doet. Hij hoort bij een apparaat en blijft altijd gelijk. `POST /v1/user/login {email, country}` stuurt een code per mail. `POST /v1/user/login {email, country, code}` koppelt het account aan de `client_id`. Staat `id` in het antwoord, dan is die `client_id` al ingelogd; `error` betekent een fout. Er is geen wachtwoord en het token wordt niet ververst. `country` is `nl` of `be`. |
| Bekende eindpunten | `GET /v1/order/count` geeft `count` (alle bestellingen) en `openOrderIds`. `GET /v1/order/{id}` geeft `data.products` en `data.deliverySlot` (`date`, `tsStart`, `tsEnd`). |
| Onbekend | Producten zoeken, productdetail (prijs, verpakkingsgrootte, aanbieding), het mandje lezen, vullen en aantallen wijzigen, aanbiedingen en favorieten. |
| Website-JS | Kent alleen marketing-eindpunten, zoals `/v1/contact`, `/v1/user/createFromWeb`, `/v1/invite/...` en `/v1/page/hit`. |
| Weekbox | `crisp.nl/weekbox` heeft een blok `window.ssrData` met 5 varianten van elk 3 recepten: Weekbox, Weekbox vega, Weekbox vegan, Snelle weekbox en Snelle weekbox vega. Het gaat alleen om de huidige week; op 1 november was dat "8 nov - 12 nov". Per recept staan erin: het aantal porties (2), ingrediëntgroepen en de stappen. Een ingrediënt heeft `productId`, `formattedName`, `searchName` en `formattedQuantity`. "Zelf toe te voegen" is een aparte groep. |
| `robots.txt` | Op `crisp.nl` en `crispapp.nl` staat `User-Agent: *` zonder `Disallow`. |

## Voorwaarden, naast die van de winkels die er al zijn

De bepalingen over tools, scrapen en account blokkeren zijn bij Crisp, Picnic en Jumbo bijna woordelijk gelijk. Albert
Heijn heeft er geen, maar verbiedt net als Picnic het uit elkaar halen van de app.

| Winkel | Informatie ophalen met tools | De app uit elkaar halen | Account blokkeren |
| --- | --- | --- | --- |
| Crisp ([algemene voorwaarden](https://crisp.nl/voorwaarden/algemeen)) | Art. 11.3: niet toegestaan zijn "(delen van) de inhoud ... herhaald en systematisch op te vragen en te hergebruiken" en "soft- of hardware tools en/of oplossingen (in eigen beheer of beschikbaar gesteld door derden) te gebruiken, voor zover deze gericht zijn op door Crisp toegankelijk gemaakte informatie over te nemen, te spideren, scrapen, doorzoeken of op andere vergelijkbare wijze te gebruiken". | Niet genoemd. Art. 11.1 geeft alleen een recht om de app te gebruiken "op je telefoon of tablet". | Art. 11.5, bij misbruik van de app of de diensten. |
| Picnic ([algemene voorwaarden](https://picnic.app/nl/algemene-voorwaarden/), laatst gewijzigd 16 februari 2026) | Art. 13: bijna dezelfde tekst. Verboden zijn het "herhaald en systematisch opvragen en hergebruiken" en "soft- of hardwaretools (laten) gebruiken die erop gericht zijn door Picnic toegankelijk gemaakte informatie over te nemen, te spideren, te scrapen, te doorzoeken". | Art. 1: "Je mag de app niet deassembleren, decompileren of met reverse engineering uit elkaar halen." | Art. 14, bij misbruik. |
| Jumbo ([algemene voorwaarden](https://www.jumbo.com/service/algemene-voorwaarden), deel II) | Art. 9.3: dezelfde tekst, plus "via robots of op enigerlei andere wijze te spideren, scrapen, doorzoeken". | Niet genoemd. | Art. 11, bij misbruik. |
| Albert Heijn ([algemene voorwaarden](https://www.ah.nl/algemene-voorwaarden)) | Geen bepaling over scrapen of tools gevonden. Art. 14 verbiedt alleen informatie openbaar te maken, te verveelvoudigen of te bewerken zonder toestemming, "behalve voor persoonlijk gebruik". | Voorwaarden AH app, art. 1: de app niet "deassembleren, decompileren of onderwerpen aan reverse engineering". Ook mag de app niet op een netwerk worden aangesloten waardoor andere apparaten hem kunnen gebruiken. | Art. 40 en 43, bij misbruik of fraude. |

De tekst van Albert Heijn is gelezen via een ophaalhulpmiddel, omdat `ah.nl` een direct verzoek weigerde (`403`); hij
is niet woord voor woord nagekeken. De andere drie zijn rechtstreeks van de pagina gelezen.

Wat dit betekent:

- MealieShopper gebruikt de app-API's van Picnic en Jumbo al onder dezelfde soort bepaling. Een Crisp-koppeling heeft
  dus hetzelfde soort risico: Crisp kan het account blokkeren. Het risico is niet nieuw en niet groter.
- Het verschil zit in het vaststellen van de eindpunten. Bij Picnic en Jumbo volgen we projecten die anderen al
  openbaar hebben gemaakt. Bij Crisp zouden we zelf moeten uitzoeken hoe zoeken en het mandje werken. Crisp noemt
  reverse engineering niet, Picnic en Albert Heijn wel.
- Dit is een vergelijking van teksten, geen juridisch advies.

## Hoe Crisp in de code zou passen

Crisp lijkt het meest op Picnic: een app-API met een token in een header, geen productpagina's (`url: None`) en een
mandje dat je kunt lezen en wijzigen. Er is geen basisklasse. Een winkel is een module met dezelfde functies (zie het
commentaar boven `store_error` in `mealieshopper/app.py`). Voor Crisp wordt dat `mealieshopper/crisp.py`:

- `search_products(query, limit=5)`, `search_product(query)` en `candidate(product, quantity, unit)`. Die gebruiken
  `packs.assess_packs` en `deals.candidate_deal`. De API moet daarvoor de verpakkingsgrootte als tekst geven (`unitSize`);
  dat is nog niet vastgesteld.
- `add_to_cart(items)` en `get_cart()`, en waar het kan `set_quantity` en `clear_cart`, zoals bij Picnic.
- `status`, `unlink` en een `CrispAuthError`.
- Inloggen in twee stappen, zoals de 2FA-stap van Picnic (`picnic.request_2fa_code` en `picnic.verify_2fa`): eerst het
  e-mailadres, dan de code uit de mail. De `client_id` gaat versleuteld in `auth.set_secret`, en het land ook.
- Routes onder `/api/crisp/` die `cart_preview`, `store_error` en `store_cart` in `app.py` hergebruiken.

Een nieuwe winkel raakt deze plekken:

- **Backend:**
  - `auth.ADMIN_PREFIXES`
  - `offers.py` (`FETCHERS`, `STORES`, `linked()` en `SOURCE_RANK`)
  - `onboarding._integrations_configured`
  - `backup.py` (`SECRET_KEYS`, `DATA_TABLES` of `EXCLUDED_TABLES`, en `COUNT_GROUPS`)
  - `audit.SERVICE_DOMAINS` (met `crispapp.nl`)
  - `about.COMPONENTS`
  - de MCP-tools en hun winkelkeuzes in `mcp.py`, en de toestemmingspagina `templates/oauth.html`
- **Frontend:**
  - `templates/index.html` (instellingen, winkelmenu en subtabs)
  - een nieuwe `static/crisp.js`
  - `static/app.js` (`state.linkedStores`, `STORES` en de subtabs)
  - `basket.js`, `offers.js`, `recipe_search.js`, `onboarding.js` en `audit.js`
- **Teksten:**
  - `static/i18n/nl.js` en `en.js`
  - `README.md` en `README.en.md`
  - de `.env`-voorbeelden
  - de Home Assistant-add-on (`homeassistant/mealieshopper/`)
- **Tests die meelopen:** `test_backup`, `test_i18n`, `test_audit`, `test_about`, `test_mcp`, `test_readme`,
  `test_ha_addon` en `test_onboarding*`.

## Besluit

De eigenaar besloot op 1 november 2026 eerst om de bevindingen alleen vast te leggen. Na de vergelijking van de
voorwaarden (Crisp is niet strenger dan Picnic en Jumbo) besloot hij dezelfde dag om Crisp in te bouwen, met de app-API
van NLthijs48/home-assistant-crisp als voorbeeld. Dat is vervolgstap B: koppelen met de code uit de mail en de
bestellingen tonen. Zoeken en het mandje vullen (stap C) wachten op de eindpunten. Er wordt geen app uit elkaar gehaald
en geen eindpunt geraden. De Weekbox komt later aan bod, als vervolgstap A.

## Wat niet is vastgesteld

- De eindpunten en de vorm van de antwoorden voor producten zoeken, productdetail, het mandje, aanbiedingen en favorieten.
- Of producten een verpakkingsgrootte als tekst hebben die `packs.assess_packs` kan lezen.
- Of een `client_id` die niet is ingelogd al producten kan zoeken.
- Of Crisp verzoeken vanaf servers of datacenters weert, zoals Picnic soms doet (`PicnicBlockedError`).
- Hoe lang Crisp de app-versie in `X-Crisp-Agent` blijft accepteren.
- Of de Weekbox ook andere weken toont dan de huidige.

## Vervolg

Om de onbekende eindpunten vast te stellen zijn er drie routes, waarvan er nog geen is gekozen:

1. **Crisp om toestemming of om een API vragen** (service@crisp.nl). Dat is de schoonste route, maar hij kan lang duren.
2. **Het verkeer van de eigen Crisp-app vastleggen** met HTTP Toolkit of mitmproxy, met het eigen account. Op Android is
   daarvoor een emulator of een aangepaste app nodig. Tokens en het e-mailadres gaan niet in de repository.
3. **De openbare Android-app lezen**: in de React Native-bundel zoeken naar de namen van eindpunten, zonder verzoeken aan
   Crisp. De voorwaarden van Crisp noemen reverse engineering niet; die van Picnic en Albert Heijn wel.

Daarna kan het in aparte stappen, elk met een eigen versie en release:

- **A. Crisp Weekbox in Maaltijdboxen.** Een nieuwe module `crisp_weekbox.py` volgens het patroon van `marleyspoon.py`
  en `hellofresh.py`:
  - één pagina per 6 uur, met een pauze en een eigen User-Agent;
  - een tabel `crisp_weekbox_menus` in `backup.EXCLUDED_TABLES`;
  - importeren naar Mealie via `mealkit_import`;
  - een extra aanbieder in `static/mealkits.js`.

  Daarvoor geldt art. 11.3.1 (herhaald en systematisch opvragen). Dat is hetzelfde soort afweging als bij HelloFresh en
  Marley Spoon.
- **B. Crisp-winkel, eerste deel.** Inloggen met de code uit de mail en de bestellingen tonen, met de eindpunten die al
  bekend zijn.
- **C. Zoeken en het mandje vullen.** Dit kan pas als de eindpunten zijn vastgesteld. Er hoort ook een `crisp_explore`
  voor de beheerder bij, zoals `picnic_explore`, die de vorm van de antwoorden laat zien.

## Bronnen

- [NLthijs48/home-assistant-crisp](https://github.com/NLthijs48/home-assistant-crisp): `custom_components/crisp/api.py`,
  `config_flow.py` en `coordinator.py`
- [crisp.nl/app](https://crisp.nl/app), [crisp.nl/weekbox](https://crisp.nl/weekbox), [crisp.nl/robots.txt](https://crisp.nl/robots.txt)
  en [crisp.nl/sitemap.xml](https://crisp.nl/sitemap.xml)
- [Algemene voorwaarden Crisp](https://crisp.nl/voorwaarden/algemeen)
- [Algemene voorwaarden Picnic](https://picnic.app/nl/algemene-voorwaarden/)
- [Algemene voorwaarden Jumbo](https://www.jumbo.com/service/algemene-voorwaarden)
- [Algemene voorwaarden Albert Heijn](https://www.ah.nl/algemene-voorwaarden)
