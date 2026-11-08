# Onderzoek: het weekmenu plannen in MealieShopper

Doel: vaststellen of MealieShopper het weekmenu niet alleen kan lezen maar ook kan maken en wijzigen, zodat je niet meer
naar Mealie hoeft om te plannen, en dat het in Mealie terechtkomt. Onderzocht op 1 november 2026 in de broncode van
Mealie (`mealie/routes/households/controller_mealplan.py` en `mealie/schema/meal_plan/new_meal.py`, tags `v2.8.0` tot en
met `v3.12.0` en `mealie-next`).

## Uitkomst: gebouwd in 2.82.0 (recepten van winkels en maaltijdboxen inplannen in 2.83.0)

| Onderdeel | Bevinding |
| --- | --- |
| Lezen | `GET /api/households/mealplans?start_date=&end_date=` (gebruikte MealieShopper al). Een notitie zonder recept heeft `"recipe": null`. |
| Aanmaken | `POST /api/households/mealplans` met `{date, entryType, recipeId}` of, voor een notitie, `{date, entryType, title, text}`. Recept óf titel is verplicht. Antwoord 201 met het hele item. |
| Wijzigen | `PUT /api/households/mealplans/{id}` met het **volledige** item, inclusief `id`, `groupId` en `userId`. Dus eerst ophalen (`GET /{id}`), dan samenvoegen. |
| Verwijderen | `DELETE /api/households/mealplans/{id}`. Het recept zelf blijft staan. |
| Verras me | `POST /api/households/mealplans/random` met `{date, entryType}`: Mealie kiest een recept volgens de planregels van het huishouden en maakt het item meteen aan. 404 als geen recept past. |
| Rechten | Een gewone CRUD-controller: elk lid van het huishouden mag dit met zijn eigen token. Er is geen extra recht nodig. |
| Maaltijden | `breakfast`, `lunch`, `dinner` en `side` in elke versie; `snack`, `drink` en `dessert` pas vanaf **Mealie v3.7.0**. Een ouder Mealie weigert die drie met 422. |
| Recepten zoeken | `GET /api/recipes?search=&page=&perPage=`; met een zoekterm sorteert Mealie zelf op relevantie. `totalTime` is vrije tekst ("1 hour 30 minutes"). |
| Al geïmporteerd? | Bij elke import bewaart Mealie de meegegeven link als `org_url` (`scraper_strategies.py`). `GET /api/recipes?queryFilter=org_url LIKE "…"` vindt het recept terug; `LIKE` is hoofdletterongevoelig en bestaat al in v1.12 (met `OR`). |
| Porties (`recipeScale`) | Sinds **Mealie v3.29.0** (PR mealie-recipes/mealie#8701) heeft elke maaltijd een schaal: `recipeScale`, een getal > 0, standaard 1. De planner van Mealie toont het als porties (de porties van het recept maal de schaal) en rekent de hoeveelheden pas bij het tonen om; het recept zelf verandert niet. Een PUT zonder het veld zet het terug op 1. De servings-knop op de receptpagina bewaart niets. |
| Weekitems | Sinds **Mealie v3.30.0** kan een maaltijd een hele week duren (`endDate`). In `mealie-next` gaf de lijst ze alleen met `include_week_entries` mee; nog niet nagegaan voor v3.30. |

## Porties uit Mealie (2.84.0, issue #159)

MealieShopper leest `recipeScale` bij elke maaltijd en rekent de ingrediënten van die maaltijd om, voor de lijst, de controlelijst, het mandje en de MCP-tool `get_week_menu`. Elke maaltijd krijgt `servings` (`scale`, `base`, `planned`); de weekplanner toont "4 porties". Een Mealie ouder dan v3.29 geeft geen `recipeScale` mee en rekent dus met schaal 1. Bij wijzigen gaat `recipeScale` mee terug naar Mealie; een ander recept begint, net als in Mealie, weer bij 1. Gelijke ingrediënten met dezelfde eenheid worden over de week opgeteld (voorheen telde alleen de eerste regel).

## Besluit: write-through, geen eigen planning

MealieShopper bewaart zelf geen weekmenu. Elke wijziging gaat meteen naar de API van Mealie en daarna wordt de week
opnieuw gelezen. Mealie blijft zo de enige bron: er is geen tweede planning die uit de pas kan lopen, geen
conflictafhandeling en geen nieuwe tabel (de back-up verandert niet). Het nadeel, dat plannen zonder Mealie niet kan,
geldt nu ook al voor het lezen van het weekmenu.

Wat er gebouwd is:

- **Weekmenu.** Altijd de hele week, met per dag **Toevoegen**. Een geplande maaltijd is een knop: wijzigen (dag,
  maaltijd, recept of notitie) en verwijderen. Een notitie (bijvoorbeeld "Uit eten") toont haar toelichting.
- **Plan-dialoog.** Dag, maaltijd (de types die de gekoppelde Mealie kent), en **Recept** (zoeken in Mealie) of
  **Notitie**. **Verras me** laat Mealie kiezen volgens de planregels.
- **Inplannen na een import.** Elke melding "Recept geïmporteerd" heeft nu **Inplannen**; de dialoog opent met het nieuwe
  recept al gekozen (de app kent dan alleen de slug; de server zoekt het id op).
- **Recepten zoeken** zoekt ook in Mealie, als eerste blok.
- **Elk recept is in te plannen.** Overal (Recepten zoeken, Winkels, Maaltijdboxen) heeft een receptkaart **Bekijken** en
  **Inplannen**. Een recept van een winkel of maaltijdbox gaat bij het opslaan eerst naar Mealie (de bestaande
  importroute, met `?reuse=1`); staat het er al met dezelfde bronlink, dan gebruikt MealieShopper dat recept. Allerhande
  zoekt op het receptnummer, omdat de link met of zonder naam kan zijn opgeslagen.
- **MCP.** `search_mealie_recipes`, `plan_meal` en `remove_planned_meal`; `get_week_menu` geeft per maaltijd het id.
  `remove_planned_meal` is als enige tool gemarkeerd als destructief; de beheerder kan elke tool uitzetten.
- **Rechten.** Iedereen die is ingelogd mag plannen, net als recepten importeren. Mealie ziet altijd de gekoppelde
  gebruiker (de token van de beheerder) als maker.

Onderweg gevonden en opgelost: bevatte een week een notitie zonder recept, dan faalde het hele weekmenu
(`"recipe": null` werd als object gelezen).

## Buiten scope

- Weekitems (`endDate`, Mealie v3.30.0): nagaan of het weekmenu ze meekrijgt. De porties (`recipeScale`) leest MealieShopper sinds 2.84.0 en stelt het sinds 4.3.0 ook in, bij *Maaltijd plannen* en *Maaltijd wijzigen* (issue #283; Mealie neemt het veld ook bij het aanmaken aan, `PlanEntryCreate.recipe_scale`).
- Slepen tussen dagen: verplaatsen gaat via de dialoog (dag kiezen), wat ook op een telefoon werkt.
- De planregels van Mealie beheren (`/api/households/mealplans/rules`): dat blijft in Mealie.
