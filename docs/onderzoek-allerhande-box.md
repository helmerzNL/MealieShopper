# Onderzoek: Allerhande Box (Albert Heijn)

Doel: vaststellen of MealieShopper het weekmenu van de Allerhande Box kan tonen in de tab **Maaltijdboxen**. Spike van
27 oktober 2026: een paar verzoeken, geen login.

## Uitkomst: nog niet toegevoegd

| Onderdeel | Bevinding |
| --- | --- |
| Pagina's van ah.nl | `www.ah.nl/allerhande/...` geeft op elk automatisch verzoek een `403 Access Denied` (ook gewone recepten en een mogelijke boxpagina). Pagina's lezen valt dus af. |
| `robots.txt` | Noemt `/allerhande/` en recept-, video- en productsitemaps, maar niets over een box. |
| AH-API | De app leest Allerhande-recepten al via de GraphQL van de mobiele API met een anoniem token (`ah.py`: `search_recipes`). Introspectie staat uit. De zoekvraag `recipeSearch` kent geen box-veld; zoeken op "allerhande box" geeft gewone recepten, geen boxmenu. |
| Weekmenu | Er is geen openbare of anonieme bron gevonden met het menu van een Allerhande Box per week. |

## Besluit

Niet bouwen. Het menu raden uit veldnamen van een API zonder schema, of de pagina's van ah.nl omzeilen, past niet bij de
afspraak om alleen openbare, toegestane bronnen te lezen. Opnieuw bekijken als AH een boxmenu in de API laat zien (bijvoorbeeld
een label op recepten of een eigen query), of als iemand een Allerhande Box-account wil koppelen; dat is dan een aparte keuze
omdat het een betaalabonnement raakt.
