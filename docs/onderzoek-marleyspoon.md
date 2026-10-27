# Onderzoek: Marley Spoon en Dinnerly (Maaltijdboxen)

Doel: vaststellen of MealieShopper het weekmenu van Marley Spoon Nederland en Dinnerly kan tonen, naast HelloFresh
(`docs/onderzoek-hellofresh.md`). Spike van 27 oktober 2026: alleen openbare pagina's en het openbare menu-eindpunt zijn
gelezen (een handvol verzoeken, geen login, geen account, geen sleutel).

## Status

Gebouwd in v2.79.0: aanbiederskeuze in de tab **Maaltijdboxen** (uitklapmenu in de subtabbalk, `static/mealkits.js`) en
`mealieshopper/marleyspoon.py`, routes `/api/marleyspoon/menu`, `/api/marleyspoon/search` en
`/api/marleyspoon/recipes/<sleutel>/import`. De gedeelde importlogica staat in `mealieshopper/mealkit_import.py` (ook voor HelloFresh).
`marleyspoon_menus` staat bewust in `backup.EXCLUDED_TABLES`. De voorwaarden van Marley Spoon moeten nog door de eigenaar zijn gelezen voordat dit
in een stabiele release komt.

## Wat is vastgesteld

| Onderdeel | Bevinding |
| --- | --- |
| `robots.txt` | Eén groep voor alle bots. Uitgesloten zijn onder meer `/checkout`, `/account`, `/orders` en `/menu/*/card` en `/menu/*/download`. `/menu` en receptpagina's (`/menu/{id}-{slug}`) zijn niet uitgesloten. |
| Menupagina | `https://marleyspoon.nl/menu` toont een week; andere weken met `?week=YYYY-MM-DD`. De kaarten worden in de browser opgebouwd uit een GraphQL-antwoord. |
| Menu-eindpunt | `POST https://api.marleyspoon.com/public/graphql` met de query `GetMenu` (velden `id`, `slug`, `title`, `subtitle`, `recipeServingTime`, `category`, `image`, `attributes`). Antwoordt zonder `Authorization` en zonder sleutel; de site stuurt voor het menu zelf ook geen token mee. Introspectie staat uit. |
| Weken | `startDate` kiest de week; het antwoord heeft `startOfWeek`. Een week zonder menu geeft een lege lijst. In de proef: 94 recepten in de huidige week, 82 in de volgende. |
| Receptpagina | `/menu/{id}-{slug}` is een schil zonder schema.org-gegevens en zonder ingrediënten of stappen in de HTML; alleen `og:title`, `og:description` en `og:image`. |
| Receptdetail | `POST https://api.marleyspoon.com/graphql` met de query `GetRecipeDetails_Web` uit de eigen frontend (`recipe(id)`: titel, ondertitel, beschrijving, `duration`, `shippedIngredients`, `assumedIngredients`, `steps`, `nutritionalInformation`). Dat eindpunt eist `Authorization: Bearer <token>`; zonder token `401`. Het token is het bezoekerstoken dat elke receptpagina aan iedere bezoeker geeft (`gon.api_token`). Op `/public/graphql` bestaat `recipe` niet. |
| Voorwaarden | Niet gelezen (zie "Wat niet is vastgesteld"). |

## Besluit

- Het menu lezen we zoals de site zelf: dezelfde vraag, zonder token, voor één week, met pauze en eigen User-Agent.
- Voor het importeren van een recept doen we wat de site zelf doet: de receptpagina ophalen, het bezoekerstoken eruit lezen en
  daarmee één vraag stellen. Dat is een ander besluit dan in de eerste versie van v2.79.0 (toen alleen menu); het is gewijzigd op
  verzoek van de gebruiker. Het token is geen account en geen geheim van de gebruiker, maar wel een sleutel van de site: het
  wordt per import opnieuw gehaald, **niet bewaard en niet gelogd** (de auditregel noemt alleen het receptadres, de status en de
  pogingen), en niet aan de browser of aan Mealie doorgegeven.
- Mealie weigert het recept (4xx): één tweede poging zonder foto en voedingswaarden. Niet via de pagina: die heeft geen receptgegevens.
- Het formaat is niet gedocumenteerd en kan zonder bericht veranderen; een afwijkend antwoord geeft een nette melding en een
  oudere kopie van het menu blijft zichtbaar.

## Dinnerly

`dinnerly.nl` (en zijn `robots.txt`) geeft een `302` naar `marleyspoon.nl`. De frontend van Marley Spoon kent Dinnerly wel als
merk (`DN`), maar de vraag `menu(country: NL, brand: DN)` geeft een lege lijst. Er is dus geen Nederlands Dinnerly-menu om te
tonen en Dinnerly is niet als aanbieder toegevoegd. Opnieuw bekijken als `dinnerly.nl` weer een eigen aanbod krijgt.

## Wat niet is vastgesteld

- **Gebruiksvoorwaarden** van Marley Spoon over geautomatiseerd gebruik en over het hergebruiken van recepttitels en foto's.
  De foto's laden we rechtstreeks van `marleyspoon.com` in de browser van de gebruiker; er wordt niets gekopieerd of bewaard.
- Of het eindpunt blijft antwoorden zonder sleutel.
