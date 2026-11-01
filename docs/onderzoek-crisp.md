# Onderzoek: Crisp (online supermarkt)

Doel: vaststellen of MealieShopper Crisp kan koppelen naast Albert Heijn, Picnic en Jumbo, zodat het weekmenu ook in het
mandje bij Crisp terechtkomt. Dit is een spike van 1 november 2026. Gelezen zijn de openbare pagina's van `crisp.nl`, de
voorwaarden van Crisp, Picnic, Jumbo en Albert Heijn, en de broncode van een open-source Home Assistant-integratie voor
Crisp. Er is niet ingelogd, er is geen account gebruikt en er zijn geen verzoeken gedaan aan de app-API van Crisp.
Daarna is de openbare Android-app gelezen en is Crisp ingebouwd (zie [Status](#status)).

## Status

Gebouwd in v2.86.0 (branch `feature/crisp`). Crisp staat als winkel in MealieShopper (`mealieshopper/crisp.py`,
`static/crisp.js`, routes onder `/api/crisp/`):

- **Wel**:
  - koppelen met de code uit de mail (Nederland of België);
  - het weekmenu via de controlelijst in het echte Crisp-mandje zetten;
  - Crisp-recepten zoeken, bekijken, importeren en inplannen;
  - het mandje bekijken in het tabblad Mandje;
  - een verkenner voor de beheerder;
  - het verkeer in het auditlog;
  - het apparaat-id in de back-up.
- **Niet (nog)**:
  - het mandje aanpassen;
  - bestellingen en favorieten onder Winkels;
  - aanbiedingen in het tabblad Aanbiedingen;
  - een stap in de installatiehulp;
  - MCP-tools;
  - de Weekbox (vervolgstap A).

De eindpunten voor zoeken, product, recepten en het mandje zijn vastgesteld uit de broncode in de openbare Android-app
(zie [Eindpunten uit de app](#eindpunten-uit-de-app)). Ze zijn nog niet met een echt account geprobeerd. De verkenner
(Instellingen, Koppelen, Crisp, **Crisp-routes verkennen**) laat na het koppelen zien of ze werken en welke velden de
antwoorden hebben.

## Uitkomst van de eerste spike

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

## Eindpunten uit de app

De eigenaar koos op 1 november 2026 om de openbare Android-app te lezen. Dat is versie 2.99 (versiecode 530), opgehaald
via Aptoide (`com.freshfoodventures.crisp`, ondertekend met de sleutel van Google Play App Signing). De app is niet
uitgevoerd en er zijn geen verzoeken aan Crisp gedaan.

De app is in React Native gebouwd. In `assets/` staan de bundel en een sourcemap met de broncode van de app, waaronder
gegenereerde API-klassen (`types/api/app/...`, "Automatically generated based on backend/app/..."). Wat MealieShopper
daaruit gebruikt:

| Route | Vorm (uit de broncode) |
| --- | --- |
| `POST /user/login {email, country}` | Antwoord `{sent}` (code gemaild), `{id}` (dit apparaat is al ingelogd) of `{error}`. `error: "webLogin"` betekent dat er geen account is. |
| `POST /user/login {email, code, country}` | Antwoord `{id}` of `{error}`. |
| `POST /user/login {logout: true}` | Afmelden, zoals de app bij uitloggen doet. |
| `GET /user` | `id` staat erin als het apparaat is ingelogd (`userIsLoggedIn`). |
| `POST /search {query, cursor: false, limit}` | `{data: [...], cursor}`. Elk item heeft `itemType` (`product`, `recipe` of `text`). Een product heeft `id`, `title`, `price` en `basePrice` (tekst, `"2.49"`), `weightIndication` (zonder gewicht toont de app "per stuk"), `images[]`, `promotion`, `supplier`, `deliverable` en `label`. |
| `GET /product/{id}` | Eén product. MealieShopper gebruikt dit alleen in de verkenner. |
| `GET /basket/main` | Het mandje: `products` is een object met het product-id als sleutel (`count`, `price`, `totalPrice` en de productvelden), plus `recipes`, `payPrice` en `deliverySlot`. |
| `PUT /basket/main {products: {id: {count, from}}}` | `count` is het nieuwe aantal; een tekst als `"+1"` is een verschil. Het antwoord is het nieuwe mandje, met `_error` als Crisp iets weigert. Met `products: "clear"` leeg je het mandje (MealieShopper doet dat niet). |
| `POST /search/recipe {query, cursor: false, limit}` | Recepten als items met `itemType: "recipe"`. `type` is `""`, `bundle`, `user` of `mealkit`. |
| `GET /recipe/{id}` | `title`, `subTitle`, `description`, `portions`, `images[]`, `cookingMethods.short.minutes`, `ingredientGroups[].ingredients[]` (`formattedName`, `formattedQuantity`, `productId`, `product` en `count`), `instructions[].instruction` (met `**vet**`) en `shareUrl`. |

**Foto's** (`images[]` bij producten en recepten) zijn blob-id's, geen adressen. De app maakt er
`https://crispapp.nl/blob/<id>?thumb=<maat>` van (`app/ui/BlobImage.tsx` en `resolveBlobUrl` in
`app/react-native/Image.tsx`). `thumb` is verplicht: een macht van 2 tot 1024, daarboven een veelvoud van 512. Het
plaatje laadt zonder `Authorization`. v2.86.0 zag dit over het hoofd en toonde geen foto's; v2.86.1 maakt de adressen
zoals de app (512 voor kaarten, 1024 voor het receptdetail en de foto in het groot).

De app stuurt haar versie in `User-Agent` (`crisp/<release>/app/android/<build>`). In de browser gaat die in
`X-Crisp-Agent`. MealieShopper volgt home-assistant-crisp: `X-Crisp-Agent: crisp/app/android/667` met
`Origin: https://crispapp.nl`, en een eigen `User-Agent` met de naam MealieShopper. In `from` noemt MealieShopper
zichzelf (`screenName: "MealieShopper"`).

## Hoe Crisp in de code past

Crisp lijkt het meest op Picnic: een app-API met een token in een header, geen productpagina's (`url: None`) en een
mandje dat je kunt lezen en wijzigen. Er is geen basisklasse. Een winkel is een module met dezelfde functies (zie het
commentaar boven `store_error` in `mealieshopper/app.py`). Voor Crisp is dat `mealieshopper/crisp.py`:

- `search_products(query, limit=5)`, `search_product(query)` en `candidate(product, quantity, unit)`. Die gebruiken
  `packs.assess_packs` en `deals.candidate_deal`. De verpakkingsgrootte (`unitSize`) komt uit `weightIndication`.
- `add_to_cart(items)` en `get_cart()`. Het mandje aanpassen (`set_quantity` en `clear_cart`, zoals bij Picnic) zit er nog
  niet in.
- `status`, `unlink`, een `CrispAuthError` en een `CrispLoginError` (een geweigerd e-mailadres of een geweigerde code).
- Recepten: `search_recipes`, `get_recipe`, `jsonld` en `source_url`, zoals in `picnic_recipes.py`.
- `explore` voor de verkenner.
- Inloggen in twee stappen, zoals de 2FA-stap van Picnic (`picnic.request_2fa_code` en `picnic.verify_2fa`): eerst het
  e-mailadres, dan de code uit de mail. De `client_id` gaat versleuteld in `auth.set_secret`, en het land ook.
- Routes onder `/api/crisp/` die `cart_preview`, `store_error` en `store_cart` in `app.py` hergebruiken.

Een nieuwe winkel raakt deze plekken. In v2.86.0 zijn ze bijgewerkt, behalve `offers.py`, `mcp.py`, `oauth.html`,
`onboarding.js` en de subtabs onder Winkels: daar doet Crisp nog niet aan mee. De `.env`-voorbeelden hoefden niet te
veranderen, want er is geen nieuwe instelling.

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
van NLthijs48/home-assistant-crisp als voorbeeld. Daarna vroeg hij ook om recepten zoeken, ze in Mealie zetten en het
Crisp-mandje vullen. Voor die eindpunten koos hij route 3: de openbare Android-app lezen (zie
[Eindpunten uit de app](#eindpunten-uit-de-app)). Er is geen eindpunt geraden. Het werk staat op de branch
`feature/crisp`. De Weekbox komt later aan bod, als vervolgstap A.

## Wat niet is vastgesteld

- Of de antwoorden met een echt account precies de vorm hebben die de broncode laat zien. De verkenner toont dat.
- Of `weightIndication` altijd een verpakkingsgrootte is die `packs.assess_packs` kan lezen. Bij twijfel markeert de
  controlelijst de regel.
- Of Crisp de `from` met de naam MealieShopper accepteert.
- Of een `client_id` die niet is ingelogd al producten kan zoeken.
- Of Crisp verzoeken vanaf servers of datacenters weert, zoals Picnic soms doet (`PicnicBlockedError`).
- Hoe lang Crisp de app-versie in `X-Crisp-Agent` blijft accepteren.
- Of de Weekbox ook andere weken toont dan de huidige.

## Vervolg

- **Na het koppelen:** draai **Crisp-routes verkennen** en vergelijk de velden met de tabel in
  [Eindpunten uit de app](#eindpunten-uit-de-app). Wijkt er iets af, pas dan `crisp.py` aan.
- **A. Crisp Weekbox in Maaltijdboxen.** De openbare pagina `crisp.nl/weekbox`, volgens het patroon van `marleyspoon.py` en
  `hellofresh.py`. Daarvoor geldt art. 11.3.1 (herhaald en systematisch opvragen), net als bij HelloFresh en Marley Spoon.
- **Verder, als daar vraag naar is:**
  - het mandje aanpassen (`PUT /basket/main` met een nieuw aantal);
  - bestellingen onder Winkels (`/order/count`, `/order/{id}` en `/order/history`);
  - aanbiedingen (`/promo/current`);
  - favorieten (`/saved`);
  - een stap in de installatiehulp;
  - MCP-tools.

## Bronnen

- [NLthijs48/home-assistant-crisp](https://github.com/NLthijs48/home-assistant-crisp): `custom_components/crisp/api.py`,
  `config_flow.py` en `coordinator.py`
- De openbare Android-app van Crisp, versie 2.99 (`com.freshfoodventures.crisp`): `assets/bundle.js` en de sourcemap ernaast
- [crisp.nl/app](https://crisp.nl/app), [crisp.nl/weekbox](https://crisp.nl/weekbox), [crisp.nl/robots.txt](https://crisp.nl/robots.txt)
  en [crisp.nl/sitemap.xml](https://crisp.nl/sitemap.xml)
- [Algemene voorwaarden Crisp](https://crisp.nl/voorwaarden/algemeen)
- [Algemene voorwaarden Picnic](https://picnic.app/nl/algemene-voorwaarden/)
- [Algemene voorwaarden Jumbo](https://www.jumbo.com/service/algemene-voorwaarden)
- [Algemene voorwaarden Albert Heijn](https://www.ah.nl/algemene-voorwaarden)
