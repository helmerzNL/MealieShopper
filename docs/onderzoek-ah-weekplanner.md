# Onderzoek: de weekplanner van de AH-app in MealieShopper

Doel: vaststellen of MealieShopper de weekplanner van de Albert Heijn-app kan lezen, en die met één knop in het weekmenu
(Mealie) kan zetten. Onderzocht op 8 november 2026.

## Uitkomst: gebouwd in 4.2.0, losse producten in 4.3.0

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
  AH). Bij elk product en elke notitie staat wat er bij Naar weekmenu mee gebeurt; eigen recepten staan er met een grijs
  label en "Gaat niet mee naar het weekmenu". Er is geen kopie in SQLite: de planning verandert vaak, en elke keer openen
  haalt hem vers op. De back-up verandert dus niet.
- **Naar weekmenu.** Zet de Allerhande-recepten op dezelfde dag in het weekmenu van Mealie, als Diner. Een recept dat
  nog niet in Mealie staat, wordt eerst geïmporteerd met de bestaande AH-import; een recept dat er al staat (zelfde
  receptnummer als bron, `mealie._source_filter`) wordt hergebruikt. Staat het recept die dag al in het weekmenu, dan
  blijft het bij die ene keer, zodat twee keer drukken niets dubbel zet. Er komt alleen iets bij; wat al in het weekmenu
  staat, blijft staan.
- **Losse producten (4.3.0).** In de AH-planner staan vaak losse producten ("AH Bakaardappeltjes", "Padifood Chinese
  tomatensoep"). Besluit van de eigenaar:
  - Heet een recept in Mealie al zoals het product (`_name_key`: zonder hoofdletters, accenten en leestekens, *AH* vooraan
    mag ontbreken; gezocht met `mealie.search_recipes`), dan komt dat recept op die dag.
  - Van de andere producten maakt MealieShopper zelf een recept, via JSON-LD (`mealie.import_via_jsonld`): elk product
    `1 <titel>` als ingrediënt, het plaatje van AH (`ah.get_products_by_ids`) en één stap "Bereid volgens de verpakking".
    Weigert Mealie het plaatje (4xx), dan nog één keer zonder.
  - **Losse producten van een dag samen als één menu** (standaard aan): de producten van een dag worden één menu. De eerste
    notitie van die dag wordt de naam, anders "A, B en C".
  - **Soep en pizza altijd los** (standaard aan, alleen met de eerste aan): soep en pizza (`ah_planner.standalone`, een
    patroon op de titel dat "soepgroente" en "pizzadeeg" overslaat) krijgen dan toch een eigen recept.
  - Met de eerste uit wordt elk product een eigen recept, met de productnaam als naam.
  - Een eigen recept heeft de productlink als bron, een menu de productlink met het kleinste nummer en `#menu-` met alle
    nummers (zoals Crisp een fragment gebruikt). Zo vindt `find_recipe_summary_by_source` een recept of menu van dezelfde
    producten terug, en maakt een volgende week niets dubbel.
  - De twee keuzes staan per gebruiker in het profiel (`planner_combine`, `planner_split_meals`). Ze stonden in 4.3.0 als
    vinkjes in het Weekplanner-paneel; sinds 4.3.1 zijn het schakelaars bij Instellingen, Voorkeuren, Supermarkt (`appearance.js`),
    en wijst het paneel ernaar.
  - In het mandje zoekt MealieShopper een ingrediënt op de productnaam; `ah.rank_search_results` zet een exacte titel
    bovenaan. Een vaste koppeling aan het product is er niet: `product_links` is voor kassabonnen.
- **Een dag per verzoek.** Een import duurt een paar seconden en `RESPONSE_TIMEOUT` is 120 seconden. Het scherm stuurt
  daarom een dag per keer naar `POST /api/ah/planner/to-meal-plan` (hooguit 20 recepten en 20 producten per verzoek),
  toont de voortgang en geeft daarna een samenvatting (gepland, nieuw in Mealie, stond er al, mislukt).
- **Rechten.** Iedereen die is ingelogd mag de planner lezen en naar het weekmenu zetten, net als plannen en importeren.
- **Fouten.** Een onderdeel dat niet lukt, houdt de andere niet op. Het antwoord van AH of Mealie staat alleen in het logboek;
  de gebruiker krijgt een algemene tekst.

## Buiten scope

- Plannen in de AH-app vanuit MealieShopper: AH weigert het, en de eigenaar wil alleen lezen.
- Eigen AH-recepten in het weekmenu, en een notitie anders dan als naam van een menu: besluit van de eigenaar.
- Een product vast koppelen aan het ingrediënt van een menu, zodat het mandje het zeker kiest: kan later, met een eigen
  koppeling in `cart_preview`.
- Een MCP-tool voor de weekplanner: kan later, met dezelfde `ah.get_week_planner`.
- Verwijderen uit het weekmenu wat niet meer in de AH-planner staat: het weekmenu van Mealie blijft leidend.
