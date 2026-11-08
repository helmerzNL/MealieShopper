# Onderzoek: de weekplanner van de AH-app in MealieShopper

Doel: vaststellen of MealieShopper de weekplanner van de Albert Heijn-app kan lezen, en die met één knop in het weekmenu
(Mealie) kan zetten. Onderzocht op 8 november 2026.

## Uitkomst: gebouwd in 4.2.0

| Onderdeel | Bevinding |
| --- | --- |
| Bron | De mobiele GraphQL van AH (`https://api.ah.nl/graphql`), dezelfde die MealieShopper al gebruikt voor bewaarde recepten, lijstjes en bestellingen. Introspectie staat bij AH uit. De vraag `recipeWeekPlannerOverview` komt uit het open-source project [olekpuchka/albert-heijn-mcp](https://github.com/olekpuchka/albert-heijn-mcp) (Apache-2.0, `src/ahapi/recipes.ts`, `getMealPlan`); alleen de vorm van de vraag is overgenomen, geen code. |
| Account | Het gebruikerstoken van de AH-koppeling (Instellingen > Koppelen > Albert Heijn), zoals bij bewaarde recepten. Zonder koppeling geeft de app de koppelmelding. |
| Inhoud | `days { date items { ... } }`, vanaf vandaag, twee weken. Een item is een `WeekPlannerRecipeItem` (Allerhande-recept: `recipeId`, `recipeTitle`, `recipe { slug }`), een `WeekPlannerMemberRecipeItem` (eigen recept van het lid: `recipeId`, `recipeTitle`), een `WeekPlannerProductItem` (`productId`, `productTitle`) of een `WeekPlannerNoteItem` (`text`). Een soort maaltijd (ontbijt, avondeten) kent de planner niet. |
| Gedeeltelijk antwoord | Een item of dag dat AH niet kan oplossen (bijvoorbeeld een recept dat niet meer bestaat) komt terug als `null`, met een fout in `errors` naast `data`. De rest is bruikbaar. `_graphql` faalde op elke fout, dus er is `_graphql_partial` bij gekomen; `_graphql` gedraagt zich als voorheen. |
| Nog nooit gepland | `recipeWeekPlannerOverview` is `null`: een lege planning, geen fout. |
| Schrijven | Volgens het voorbeeldproject weigert AH het plannen via de API. MealieShopper probeert het niet: de eigenaar wil alleen uitlezen. |
| Eigen recepten | Geen Allerhande-recepten: `recipe(id)` en de JSON-LD-import werken er niet mee. Ze zijn dus niet in Mealie te zetten. |

## Besluit: lezen bij AH, recepten naar Mealie, geen eigen planning

Wat er gebouwd is:

- **Winkels > Albert Heijn > Weekplanner** (`/stores/ah/planner`). Per dag wat in de AH-app gepland staat, in de vorm
  van het weekmenu. Een Allerhande-recept opent met een tik in MealieShopper (met Inplannen, Importeer en de link naar
  AH). Eigen recepten, producten en notities staan er met een grijs label en "Gaat niet mee naar het weekmenu". Er is geen
  kopie in SQLite: de planning verandert vaak, en elke keer openen haalt hem vers op. De back-up verandert dus niet.
- **Naar weekmenu.** Zet de Allerhande-recepten op dezelfde dag in het weekmenu van Mealie, als Diner. Een recept dat
  nog niet in Mealie staat, wordt eerst geïmporteerd met de bestaande AH-import; een recept dat er al staat (zelfde
  receptnummer als bron, `mealie._source_filter`) wordt hergebruikt. Staat het recept die dag al in het weekmenu, dan
  blijft het bij die ene keer, zodat twee keer drukken niets dubbel zet. Er komt alleen iets bij; wat al in het weekmenu
  staat, blijft staan. Alleen recepten gaan mee: besluit van de eigenaar.
- **Een dag per verzoek.** Een import duurt een paar seconden en `RESPONSE_TIMEOUT` is 120 seconden. Het scherm stuurt
  daarom een dag per keer naar `POST /api/ah/planner/to-meal-plan` (hooguit 20 recepten per verzoek), toont de voortgang
  en geeft daarna een samenvatting (gepland, nieuw in Mealie, stond er al, mislukt).
- **Rechten.** Iedereen die is ingelogd mag de planner lezen en naar het weekmenu zetten, net als plannen en importeren.
- **Fouten.** Een recept dat niet lukt, houdt de andere niet op. Het antwoord van AH of Mealie staat alleen in het logboek;
  de gebruiker krijgt een algemene tekst.

## Buiten scope

- Plannen in de AH-app vanuit MealieShopper: AH weigert het, en de eigenaar wil alleen lezen.
- Eigen AH-recepten, producten en notities in het weekmenu: besluit van de eigenaar. Een product zou als notitie kunnen,
  of later direct naar het mandje.
- Een MCP-tool voor de weekplanner: kan later, met dezelfde `ah.get_week_planner`.
- Verwijderen uit het weekmenu wat niet meer in de AH-planner staat: het weekmenu van Mealie blijft leidend.
