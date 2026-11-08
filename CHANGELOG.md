# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub; een stabiele release krijgt de teksten van alle versies sinds de vorige stabiele release.

## v4.3.0

### Nieuw in 4.3.0

Issue: [#283](https://github.com/helmerzNL/MealieShopper/issues/283).

*English summary: MealieShopper now shows **calories**. A recipe and a planned meal show the kcal per serving and the nutrition per serving, from Mealie or, for a store or meal kit recipe, from that store; a meal also shows the total for the planned servings ("490 kcal per serving · 980 kcal in total"). **Plan a meal** and **Edit meal** let you choose the number of servings (4 becomes 2): MealieShopper stores it in Mealie as `recipeScale`, like Mealie's planner (needs Mealie 3.29), and the ingredients and the kcal total follow; per serving it stays the same. The recipe picker and **Find recipes** have a **Kcal per serving** min/max filter; in Find recipes the search term may then be empty and only your Mealie recipes are searched, since the stores give no calories in their search results. Because Mealie's recipe list has no nutrition and Mealie stores it as text, MealieShopper keeps the kcal per recipe in a cache of its own (`mealie_recipe_nutrition`, filled from every recipe it fetches from Mealie and refreshed by `updatedAt` at most every ten minutes when you search by calories; not part of the backup). The MCP tool `search_mealie_recipes` takes `kcal_min` and `kcal_max` (kcal per serving) and may then be called without a query, and returns `kcalPerServing`; `get_week_menu` returns `kcalPerServing` and `kcalTotal`, and `plan_meal` takes `servings`. Recipes imported from **Albert Heijn** now take their nutrition along into Mealie (the AH GraphQL `recipe.nutritions`, per serving), also in the bulk import and *To week menu*; the administrator fills in older AH recipes with **Fill in nutrition** under **Settings, Link, Mealie** (only recipes with an Allerhande source link and without calories; what is there stays). **Jumbo** salt is no longer stored as sodium: 1 g of salt becomes 400 mg of sodium. A new switch **Show calories** under **Settings, Preferences, Appearance** (on by default, per user) hides all of it.*

- **Calorieën bij een recept en een maaltijd.** De kcal per persoon (per portie) staan op de kaarten bij Recepten zoeken, in de receptkiezer van *Maaltijd plannen*, in het detail van een recept en bij een geplande maaltijd in het weekmenu. Het venster van een maaltijd toont ook het totaal voor de geplande porties (*490 kcal p.p. · 980 kcal totaal*) en onder het recept de voedingswaarden per portie (energie, eiwit, vet, koolhydraten, natrium en wat Mealie verder weet). Ze komen uit het blok `nutrition` van Mealie, dat MealieShopper eerst overal wegliet, of bij een recept van een winkel of maaltijdbox uit die winkel. `mealie.recipe_detail` geeft `nutrition` en `kcal`, `/api/mealplan` per maaltijd `kcal: {perServing, total}` en `nutrition`, en de kaarten van `/api/mealie/recipes` hebben `kcal` (uit de cache, of `null`).
- **Porties kiezen bij het inplannen.** Onder het gekozen recept in *Maaltijd plannen* en *Maaltijd wijzigen* staat een veld **Porties** met − en +, met de kcal per persoon en het totaal ernaast. Het begint bij de geplande porties, of bij die van het recept. Alleen een veranderd aantal gaat mee naar Mealie, als `recipeScale` = porties / porties van het recept (hoogstens 20), ook bij een nieuw recept voor een bestaande maaltijd. Het veld staat er alleen als Mealie het kent (`/api/mealplan/options` geeft `recipeScale`, vanaf Mealie 3.29) en de porties van het recept bekend zijn; een ouder Mealie krijgt een duidelijke melding. Mealie accepteert het veld ook bij het aanmaken (`PlanEntryCreate.recipe_scale`). Een notitie heeft geen porties.
- **Zoeken op calorieën.** Twee velden *Kcal per persoon* (min. en max.) in de receptkiezer en bij Recepten zoeken; `GET /api/mealie/recipes` leest `kcalMin` en `kcalMax` (hele getallen van 0 tot 5000, omgedraaid als min boven max staat). Bij Recepten zoeken mag de zoekterm dan leeg zijn en zoekt alleen het Mealie-blok; de andere winkels gaan uit beeld. Met een zoekterm bepaalt Mealie welke recepten passen en in welke volgorde; zonder zoekterm komen ze op naam uit de cache. Recepten zonder calorieën vallen weg; het aantal staat erbij (`withoutKcal`).
- **Een cache van de kcal per recept** (`mealieshopper/recipe_nutrition.py`, tabellen `mealie_recipe_nutrition` en `mealie_recipe_nutrition_meta`). Mealie geeft in zijn lijst geen voedingswaarden en bewaart ze als tekst, dus Mealie kan niet op getal filteren. Elk recept dat MealieShopper uit Mealie ophaalt (`mealie.get_recipe`) komt in de cache. Een zoekopdracht op calorieën start, als de cache ouder is dan tien minuten, een ronde die de lijst van Mealie leest en alleen nieuwe recepten en recepten met een andere `updatedAt` ophaalt (hooguit 2.000, acht tegelijk), en verdwenen recepten weghaalt. Een zoekopdracht wacht daar hooguit 15 seconden op (MCP 25); daarna komt wat er al is, met de voortgang (`indexing: {done, total}`), en het scherm zoekt na twee seconden opnieuw. Een ander Mealie-adres begint opnieuw. Mislukt een ronde, dan probeert de volgende zoekopdracht het pas na een minuut weer. De cache staat in `backup.EXCLUDED_TABLES`: hij bouwt zich vanzelf op.
- **MCP.** `search_mealie_recipes` heeft `kcal_min` en `kcal_max` (kcal per persoon); met een van de twee mag `query` leeg zijn. Elk recept heeft `kcalPerServing`, en met een grens geeft het antwoord `withoutKcal` en een `note` (recepten zonder calorieën, of de cache die zich nog vult). `get_week_menu` geeft per maaltijd `kcalPerServing` en `kcalTotal`, `plan_meal` heeft `servings` (alleen bij een recept met bekende porties, vanaf Mealie 3.29).
- **Voedingswaarden van Albert Heijn.** De receptvraag aan de GraphQL van AH heeft een nieuwe eerste variant met `nutritions { energy protein fat saturatedFat carbohydrates sugar fibers sodium { value unit } }` (per persoon, natrium in mg; op 8 november opgezocht met het anonieme token). Kent AH het veld niet meer, dan valt `_graphql_variants` terug op de oude varianten en gaat de import zonder voedingswaarden door. `ah.get_recipe_jsonld` zet ze als schema.org-`nutrition` in het recept voor Mealie, dus ook de bulkimport, *Naar weekmenu* en de receptindex van de aanbiedingen krijgen ze; `ah.recipe_detail` toont ze.
- **Oude AH-recepten aanvullen** (alleen de beheerder). **Voedingswaarden aanvullen** onder *Instellingen, Koppelen, Mealie* loopt als achtergrondtaak de recepten in Mealie langs. Bij een recept met een Allerhande-bronlink en zonder calorieën haalt hij de voedingswaarden bij AH op en zet ze met `PATCH /api/recipes/{slug}` in Mealie, als tekst zonder eenheid zoals Mealie ze bewaart; wat er al staat, blijft staan. Anderhalve seconde tussen twee vragen aan AH, stoppen bij 403 of 429 van AH of na vijf fouten achter elkaar, en **Stoppen** houdt hem eerder op. Routes `GET`, `POST` en `DELETE /api/mealie/nutrition/backfill` (in `auth.ADMIN_PATHS`); het auditlog krijgt een regel met de aantallen.
- **Jumbo: zout is geen natrium.** Jumbo geeft *zout*; dat kwam als `sodiumContent` in Mealie, en Mealie rekende "1.5 g" om naar 1500 mg natrium. Nu wordt het 600 mg (1 g zout = 400 mg natrium), tenzij Jumbo natrium zelf geeft. Ook *waarvan verzadigd* en *waarvan suikers* komen nu mee, en energie in kJ wordt kcal. De detailweergave van Jumbo, HelloFresh en Marley Spoon toont de voedingswaarden met vertaalde namen; HelloFresh en Marley Spoon gaven daar eerst niets.
- **Calorieën tonen.** Een nieuwe schakelaar onder *Instellingen, Voorkeuren, Weergave*, standaard aan, per gebruiker (kolom `user_profiles.show_calories`, gaat mee in de back-up). Uit zet `theme.js` de klasse `no-calories` op `<html>`, en verbergt de CSS de kcal, de voedingswaarden, het kcal-totaal bij de porties en de velden om op te zoeken; een zoekopdracht stuurt dan geen grenzen mee.
- **Gedeelde helper** `mealieshopper/nutrition.py`: de velden van schema.org en Mealie met hun eenheid, getallen lezen uit elke bron (`"490"`, `"490 kcal"`, `"20,5 g"`, kJ), omrekenen (kJ naar kcal, g naar mg, zout naar natrium) en terugschrijven voor een import of voor Mealie. HelloFresh en Marley Spoon gebruiken zijn lijst van velden.
- **Handleiding en README's** (NL en EN): een nieuw kopje *Calorieën*, en het weekmenu, Recepten zoeken, Voorkeuren, de Mealie-koppeling en de MCP-tools.
- **Tests:** `tests/test_nutrition.py` (lezen, omrekenen, terugschrijven), `tests/test_recipe_nutrition.py` (de cache, een ronde die alleen gewijzigde recepten ophaalt, een ander Mealie, een mislukte ronde, zoeken met en zonder zoekterm, pagina's, het gedeeltelijke antwoord tijdens het inlezen, de grenzen uit een verzoek, de route, het aanvullen met zijn stopregels en de beheerdersroute), `tests/test_calories_ui.py` (de functies voor het filter in Node, en de bedrading), plus nieuwe gevallen in `test_recipe_import.py`, `test_recipe_reuse.py`, `test_jumbo_recipes.py`, `test_hellofresh.py`, `test_marleyspoon.py`, `test_meal_plan_edit.py` (kcal per maaltijd, de porties bij plannen en wijzigen, een ouder Mealie), `test_mcp.py`, `test_profile.py`, `test_backup.py` en `test_meal_recipe_ui.py`. De woordenboektest in `test_linking_ui.py` geeft het script nu via stdin aan Node: met het hele Nederlandse woordenboek was het groter dan wat Linux als één argument toestaat (128 KiB).

## v4.2.1

### Gewijzigd in 4.2.1

Pull request: [#284](https://github.com/helmerzNL/MealieShopper/pull/284).

*English summary: the **About** page now links to the MealieShopper repository on GitHub (https://github.com/helmerzNL/MealieShopper), with a button under the version and the description. It opens in a new tab. `/api/about` also returns the address as `repoUrl`.*

- **Over: link naar MealieShopper op GitHub.** Onder het versienummer en de omschrijving staat de knop **MealieShopper op GitHub** (met het GitHub-icoon van Material Design Icons), naar [github.com/helmerzNL/MealieShopper](https://github.com/helmerzNL/MealieShopper). Hij opent in een nieuw tabblad (`target="_blank"`, `rel="noopener noreferrer"`), zoals de koffieknop. `/api/about` geeft het adres ook terug, als `repoUrl` (`about.REPO_URL`).
- **Handleiding** (NL en EN): bij Over en updates staat de knop naar GitHub, en de screenshot van Over (`docs/screenshots/nl/about.png` en `en/about.png`) is opnieuw gemaakt.
- **Tests:** `test_repo_link` controleert het adres in de API en op de pagina, het nieuwe tabblad en de vertaling in het Nederlands en Engels; de volgordetest zet de knop tussen de versie en de componenten.

## v4.2.0

### Nieuw in 4.2.0

Pull request: [#282](https://github.com/helmerzNL/MealieShopper/pull/282).

*English summary: **Stores, Albert Heijn, Week planner** (`/stores/ah/planner`) shows what you planned in the week planner of the AH app, per day from today (AH returns two weeks): Allerhande recipes, your own recipes, products and notes. MealieShopper only reads it, through the same unofficial GraphQL of the AH app as the saved recipes (`recipeWeekPlannerOverview`, with the user token of the AH link); nothing goes back to AH. **To week menu** puts the Allerhande recipes on the same day in the Mealie week menu, as dinner. A recipe that is not in Mealie yet is imported first; one that is already there (same recipe number as source) is reused. A recipe already planned on that day is not added again, so pressing twice adds nothing twice, and what is already in the week menu stays. Your own recipes, products and notes are shown but not copied. The screen sends one day per request and shows the progress and a summary afterwards. A partial answer from AH (an entry AH cannot resolve) still shows the rest. Everyone who is logged in may use it, like planning.*

- **De weekplanner van de AH-app bij Winkels.** Een nieuwe subtab **Weekplanner** bij Albert Heijn (`/stores/ah/planner`, na **Recepten**) toont per dag wat je in de weekplanner van de AH-app hebt gepland, vanaf vandaag (AH geeft twee weken): Allerhande-recepten met plaatje, eigen recepten, producten (met een link naar ah.nl) en notities. Een Allerhande-recept open je met een tik, met daar **Inplannen**, **Importeer** en de link naar AH. MealieShopper leest alleen: plannen doe je in de AH-app, en er gaat niets terug naar AH. De planning wordt niet bewaard; elke keer openen (en `Ophalen bij AH`) haalt hem vers op, dus de back-up verandert niet.
- **Naar weekmenu.** Zet alle Allerhande-recepten uit de planner op dezelfde dag in het weekmenu van Mealie, als diner (de AH-planner kent geen soort maaltijd). Staat een recept nog niet in Mealie, dan importeert MealieShopper het eerst met de bestaande AH-import; staat het er al (hetzelfde receptnummer als bron), dan gebruikt het dat recept. Staat het recept die dag al in het weekmenu, dan komt het er niet nog een keer bij: twee keer drukken zet niets dubbel. Wat al in het weekmenu staat, blijft staan. Eigen recepten, producten en notities staan er met een grijs label en gaan niet mee (besluit van de eigenaar). Het scherm stuurt een dag per verzoek (`POST /api/ah/planner/to-meal-plan`, hooguit 20 recepten), toont de voortgang en daarna een samenvatting: hoeveel recepten zijn gezet, hoeveel daarvan nieuw in Mealie, hoeveel er al stonden en welke mislukten. Een recept dat mislukt, houdt de andere niet op; het antwoord van AH of Mealie staat alleen in het logboek. Zonder Mealie-koppeling of zonder Allerhande-recepten staat de knop uit. Daarna ververst het weekmenu de geraakte weken.
- **Uitlezen bij AH.** `ah.get_week_planner` vraagt `recipeWeekPlannerOverview` aan de mobiele GraphQL van AH met het gebruikerstoken van de koppeling, en probeert het bij een geweigerd token één keer opnieuw met een vers token. De vorm van de vraag komt uit het open-source project olekpuchka/albert-heijn-mcp (Apache-2.0); introspectie staat bij AH uit. Een item of dag dat AH niet kan oplossen komt als `null` met een fout ernaast: de rest wordt getoond met een melding dat AH niet alles gaf. Daarvoor is er `_graphql_partial`; `_graphql` faalt zoals voorheen op elke fout. Nog nooit gepland is een lege planning. De plaatjes komen uit `recipeSummaries`; lukt dat niet, dan blijft de planning zonder plaatjes. Zonder AH-koppeling zegt de melding waar je koppelt.
- **Rechten.** Iedereen die is ingelogd mag de weekplanner lezen en naar het weekmenu zetten, net als plannen en importeren; de routes staan niet bij de beheerdersroutes.
- **Handleiding en README's** (NL en EN): de weekplanner bij Winkels, met een screenshot, en de AH-rij van de koppelingen. Onderzoek en besluit in `docs/onderzoek-ah-weekplanner.md`.
- **Tests:** `tests/test_ah_planner.py` voor het uitlezen (alle soorten items, een onbekende soort en `null`, een gedeeltelijk antwoord, alleen fouten, een lege planning, de plaatjes, een vreemde slug, een geweigerd token), de invoercontrole, het overzetten (importeren, hergebruiken, niet dubbel plannen, hetzelfde recept op twee dagen één import, een mislukte import houdt de rest niet op) en de routes (ook een gewone gebruiker). `tests/test_ah_items_ui.py` kent de nieuwe subtab, en de screenshots hebben een scenario `ah-planner`.

## v4.1.1

### Gewijzigd in 4.1.1

Pull request: [#281](https://github.com/helmerzNL/MealieShopper/pull/281).

*English summary: **Export for data.mealieshopper.nl** no longer includes the address of a leaflet archive (supermarktaanbiedingen.com, voordeelmuis.nl): such a line goes to the site with an empty `url`, the line itself (product, price, period, offer text) still goes. The JSON Schema the export writes per store (`schemas/<store>.schema.json`) refuses such an address, so the site's check rejects a submission that has one. Lines that only differed in that address (the archive listed the same offer twice) are exported once: 283 of the 33,000 from wolph's gist. Importing is unchanged: a line without an address is matched on store, product and period, so importing it again still finds it, and of two variants of one product in the same week another installation keeps one (57 lines from the gist). Pull request [#281](https://github.com/helmerzNL/MealieShopper/pull/281).*

- **Exporteren voor data.mealieshopper.nl: geen adres van een folderarchief.** Op data.mealieshopper.nl horen geen bronadressen van supermarktaanbiedingen.com. Van een regel uit een folderarchief (`price_archive_tidy.ARCHIVE_URLS`: supermarktaanbiedingen.com en voordeelmuis.nl) is `url` in de export nu leeg; de regel zelf, met product, prijs, looptijd en actietekst, gaat wel mee. Een adres van de winkel zelf (zoals `www.ah.nl`) blijft staan.
- **Het schema weigert zo'n adres.** `schemas/<winkel>.schema.json` heeft bij `url` een `not` met het begin van die adressen (`ARCHIVE_URL_PATTERN`, alleen `.` en `?` geëscapet, zodat het ook als ECMAScript-regex werkt). Een inzending met zo'n adres komt zo niet door de check **Inzending controleren** van de site; de documenten van de gist van wolph dus ook niet zonder dat het adres eruit is.
- **Dubbele regels één keer.** Het archief noemt dezelfde aanbieding soms twee keer, op twee folderpagina's. Zonder adres zijn zulke regels gelijk; de export neemt ze één keer op (`merged` in de kop, en een zin in `MEALIESHOPPER-EXPORT.md`). Bij de gist van wolph zijn dat er 283 van de 33.000.
- **Terug importeren blijft werken.** Een regel zonder adres herkent de import aan winkel, product en looptijd: een tweede import slaat hem over. Een regel met een `folder:`-id blijft een archiefregel voor **Vervangen**. Daardoor komt van twee varianten van hetzelfde product in dezelfde week (zoals *Pak 100 gram* en *Stuk 100 gram*) bij een andere installatie maar één binnen: bij de gist 57 regels.
- **Handleiding** (NL en EN): de export voor de site zegt dat het adres van een folderarchief niet meegaat.
- **Tests:** de export heeft geen adres van supermarktaanbiedingen.com meer in een document, de rondreis geeft dezelfde regels terug met een leeg adres, en het schema weigert adressen van supermarktaanbiedingen.com en voordeelmuis.nl, ook bij een prijs.

## v4.1.0

### Nieuw in 4.1.0

Pull request: [#279](https://github.com/helmerzNL/MealieShopper/pull/279).

*English summary: MealieShopper now speaks the layout of [data.mealieshopper.nl](https://data.mealieshopper.nl), the shared, reviewed data site for MealieShopper. **Import price archive** reads wolph's gist for the data site (`data-site-manifest.json` with Base64 parts of the ZIP to submit; every part, the ZIP, every document and the number of lines per document are checked against the manifest) and any ZIP with documents in that layout: lists of lines in `offers/` and `prices/` per store, such as the ZIP `restore_data.py` makes, the data repository or the new export. The new button **Fetch offers** reads `https://data.mealieshopper.nl/index.json`, fetches the documents of the store collections (only from the site itself, no redirects, length and SHA-256 from the catalogue) and shows the same summary as an upload before anything is imported. A new box **Also keep lines that belong to none of your products** makes the existing `keep_unlinked` available on the screen, for uploads and fetching. **Export for data.mealieshopper.nl** gives the offers and prices as separate JSON files per store and year (`data/<store>/offers/<yyyy>-<nnn>.json`, `data/<store>/prices/…`, at most 4 MiB each, named like the gist) with a JSON Schema per store (`schemas/<store>.schema.json`), byte for byte as the site publishes them, ready to submit in helmerzNL/data.mealieshopper.nl. Through MealieShopper, leaflet lines are tidied as before: combination offers drop out (62,155 of the gist's 95,155 AH lines; with the box 33,000 remain), and fields the app does not keep (`source_title`, `raw_old`, `raw_now`, `reason`, `match_*`) are not exported. The pack check of the tidying now also reads the short offer text of a line without `description`, so a line exported by the app keeps its price when it is imported again. Pull request [#279](https://github.com/helmerzNL/MealieShopper/pull/279).*

- **De gist voor data.mealieshopper.nl importeren.** **Prijsarchief importeren** leest de ZIP van de gist van wolph zoals **Download ZIP** hem geeft: `data-site-manifest.json` met per winkel de Base64-delen van de ZIP die je op de site instuurt. De app controleert de lengte en SHA-256 van elk deel, de ZIP, elk document en het aantal regels per document; een document dat ontbreekt of niet in het manifest staat, weigert het pakket. `restore_data.py` en het schema uit de gist leest of draait hij niet. De 95.155 AH-regels uit de gist van 7 november 2026 komen er zo door de gewone controle en het opschonen.
- **De vorm van data.mealieshopper.nl.** Ook een gewone ZIP met documenten in die vorm importeert: per winkel lijsten van regels in `offers/` (aanbiedingen) en `prices/` (prijzen), zonder `type`, zoals de ZIP die `restore_data.py` maakt, de datarepo (**Code, Download ZIP**) en de nieuwe export. Elk document moet strikte JSON zijn (geen sleutel twee keer, geen `NaN`) en een lijst; staat er een winkel boven de map, dan hoort elke regel bij die winkel. Een fout noemt het document en de plek in de lijst.
- **Aanbiedingen ophalen.** Een nieuwe knop onder **Prijsarchief importeren** haalt de aanbiedingen en prijzen van data.mealieshopper.nl op: de catalogus `index.json`, en daaruit de documenten in `offers/` en `prices/` van de collecties `ah`, `jumbo`, `picnic` en `crisp`. Alleen van de site zelf (geen doorverwijzing, geen adres uit de catalogus), met de lengte en SHA-256 uit de catalogus. Daarna volgt dezelfde controle en samenvatting als bij een upload, met de commit van de site als bron; pas **Importeren** voegt toe. `POST /api/price-archive/fetch`, alleen voor de beheerder.
- **Vinkje *Ook regels bewaren die bij geen product van jou horen*.** Wat eerst alleen via de API kon (`?keep_unlinked=1`), staat nu in het scherm, voor uploaden en ophalen. Nodig om de folderregels van de gist weer te kunnen exporteren; combi-aanbiedingen blijven altijd weg.
- **Exporteren voor data.mealieshopper.nl.** Een tweede knop onder **Prijsarchief exporteren** geeft een ZIP in de vorm van de datarepo: `data/<winkel>/offers/<jjjj>-<nnn>.json` en `data/<winkel>/prices/<jjjj>-<nnn>.json` (een nieuw volgnummer zodra een bestand 4 MiB zou passeren, zoals in de gist), `schemas/<winkel>.schema.json` (JSON Schema 2020-12 met dezelfde regels als de import; de extra velden van de gist mogen erin) en `MEALIESHOPPER-EXPORT.md` met hoe je het instuurt. Elk bestand staat er zoals de site het publiceert, dus de SHA-256 op de site is die van het bestand, en dezelfde gegevens geven dezelfde bestanden. Persoonlijke aanbiedingen gaan er nooit in. `POST /api/price-archive/export?format=site`; in het auditlog met `format: site`.
- **Opschonen: de verpakking ook uit de actietekst.** Een regel zonder `description` (zoals uit een export van de app) noemt zijn verpakking vaak alleen nog in de korte actietekst. `price_archive_tidy.known_pack` kijkt daar nu ook, als extra kans: zonder dat verloren bij de rondreis gist → export → import 2.159 van de 33.000 AH-regels hun prijs, nu 1. Voor regels mét `description` (de gists) verandert er niets.
- **Handleiding en README's** (NL en EN): de vorm van data.mealieshopper.nl, **Aanbiedingen ophalen**, het vinkje en de export voor de site, met hoe je die instuurt.
- **Tests:** `tests/test_price_archive_site.py` voor de gist (ook elke beschadiging of bewerking van het manifest, de delen en de documenten), de ZIP van de site en de datarepo, de export (de regels van de site nagebouwd: namen, grootte, opmaak, jsonschema op elk document) met de rondreis terug naar de import, en het ophalen met respx (catalogus, controlesommen, lege site, geen doorverwijzing). `jsonschema` staat erbij in `requirements-dev.txt`.

## v4.0.1

### Opgelost in 4.0.1

*English summary: a factory reset now also removes staged price archives (`.price-archive-*`: an unfinished chunked upload, an archive still being checked, rows waiting to be confirmed, and a `.job` left by a hard stop during an import). Since 3.14.0 it only removed staged backups (`.restore-*`), so a "fresh" installation could still hold the old installation's price data until someone staged a new archive. The reset now uses one list of staging prefixes, `factory_reset.STAGED_PREFIXES`, and a test fails when a module adds a prefix the reset does not know. Fixes [#276](https://github.com/helmerzNL/MealieShopper/issues/276).*

- **Fabrieksinstellingen: ook klaargezette prijsarchieven weg** ([#276](https://github.com/helmerzNL/MealieShopper/issues/276)). De reset verwijderde de database, `secret.key`, de map `images` en klaargezette back-ups (`.restore-*`), maar niet wat het prijsarchief klaarzet (`.price-archive-*`, sinds 3.14.0). Daardoor bleven na een reset een upload in stukken die nog niet af was (`.part`), een archief dat nog gecontroleerd werd (`.upload`), prijsregels die op bevestiging wachtten (`.rows`) en, na een harde stop tijdens een import, een `.rows.job` staan, met gegevens van de oude installatie erin. Nu gebruikt `factory_reset` één lijst met voorvoegsels, `STAGED_PREFIXES` (`backup.STAGE_PREFIX` en `price_archive.STAGE_PREFIX`), en verwijdert hij alles wat daarmee begint, alleen direct in de data-map.
- **Tests:** `test_staged_backups_and_price_archives_go_too` zet voor beide voorvoegsels een `.part`, `.upload`, `.rows` en `.rows.job` neer en controleert dat de reset ze verwijdert en teruggeeft, en dat een gelijknamig bestand in een submap blijft staan. `test_every_staging_prefix_of_the_app_is_known_to_the_reset` faalt als een module een `STAGE_PREFIX` krijgt dat niet in de lijst staat. Daarmee klopt de docstring van `uploads.py` weer.
- **Handleiding** (NL en EN): wat de fabrieksinstellingen nog meer weghalen (de afbeeldingencache en wat klaarstaat om terug te zetten of te importeren).

## v4.0.0

### Gewijzigd en opgelost in 4.0.0

*English summary: MealieShopper now runs on asyncio. Quart replaces Flask, Hypercorn (one process) replaces Gunicorn, HTTPX replaces Requests and aiosqlite reads and writes the database. A slow store, photo or Mealie no longer holds a thread: the app waits for it and serves the other requests in the meantime. Background work (the nightly offers run, the bulk import, the AI search) runs as tasks in the same process, and the app stops them cleanly when it shuts down. The price archive (import and export) and the upload in parts that came with 3.14 to 3.22 run on asyncio too. Routes and JSON stay the same, apart from the few deliberate differences listed below. Sources without a User-Agent of their own now send that of Chrome 157 on Android instead of the default of Requests. After a factory reset the restart policy of the container starts the app again. The container runs one Hypercorn process (`--workers 0`): the app receives SIGTERM itself and shuts down cleanly, and a crash gives a real exit code. `GUNICORN_CMD_ARGS` and `WEB_CONCURRENCY` are no longer read, so remove a custom `command: gunicorn ...` from your compose file or change it to Hypercorn. A request body may be 16 MiB, a backup or price archive in one request 200 MB and a price import 32 MiB. Downloading a backup or a price archive export has no time limit, and an upload in one request only stops with a 408 after 120 seconds without data, a part of an upload in parts after 30 seconds as in 3.22. The login proxy for Albert Heijn no longer passes MealieShopper's own cookies, forwarding headers or Authorization on to login.ah.nl, and the audit log now hides the token after "Authorization: Bearer" as well. A refreshed token of AH, Jumbo or Picnic is no longer lost when the browser goes away halfway. Proxy and CA variables work as in 3.x and are now documented in the README. There was no before-and-after measurement: a second installation with the real AH account next to production was not worth the risk. Reload the page once after updating.*

- **Asyncio in plaats van threads.** Quart vervangt Flask, Hypercorn (één proces, `--workers 0`) vervangt Gunicorn, HTTPX vervangt Requests en aiosqlite leest en schrijft de SQLite-database. Een trage winkel, productfoto of Mealie houdt geen thread meer vast: de app wacht erop en bedient intussen de andere verzoeken. Wat op de achtergrond loopt (de nachtelijke aanbiedingenrun, de bulk-import, het zoeken met AI, de koppelronde) is een taak in hetzelfde proces (`mealieshopper/tasks.py`). Bij het afsluiten stopt de app die taken en sluit hij de verbindingen. De pools van threads zijn semaforen met dezelfde grenzen (zes regels tegelijk in de winkelpreview, acht recepten tegelijk in de receptindex), en de pauzes en de stop bij 403 en 429 zijn gelijk gebleven. Rekenwerk (scrypt, de controle van een passkey, de fasen van de back-up, het uitpakken, controleren, invoegen en exporteren van een prijsarchief, het wegschrijven van een grote afbeelding of van een stuk van een upload, het omzetten van de export van het auditlog) en het wissen van de afbeeldingencache bij een fabrieksreset draaien in een thread naast de event loop.
- **Prijsarchief en uploaden in stukken, uit 3.14 tot en met 3.22.** Het importeren en exporteren van een prijsarchief en het uploaden in stukken van een back-up of prijsarchief werken zoals in 3.22.0, op asyncio. Het uitpakken en controleren, het invoegen en het exporteren lopen elk als geheel in een thread met een eigen verbinding met de database, zoals de fasen van de back-up. De import zelf is een taak in plaats van een thread. Sluit de app af terwijl hij loopt, dan heet de import onderbroken en is er niets gewijzigd, net als na een herstart. Was het invoegen al bezig en komt het nog tot het einde, dan heet de import klaar: die stand gaat mee in dezelfde transactie als de regels, zoals bij het terugzetten van een back-up. Een stuk van een upload wordt eerst helemaal gelezen, zonder slot, en daarna onder een slot weggeschreven: een stuk dat blijft steken, houdt de andere niet op. De 30 seconden stilte per stuk telt nu op het lichaam van het verzoek, in 3.x op de socket van Gunicorn. Valt de verbinding weg terwijl een stuk binnenkomt, dan stopt de route voordat er iets is geschreven, en het scherm stuurt het stuk opnieuw.
- **Requests is weg.** Over en de README's tonen Quart, Hypercorn, HTTPX en aiosqlite in plaats van Gunicorn en Requests. Flask en Werkzeug staan er nog, als basis onder Quart, en staan vast in `requirements.txt` (`flask==3.1.3`, `werkzeug==3.1.9`): Quart vraagt alleen `flask>=3.0` en `werkzeug>=3.0`, zonder bovengrens. Flask stond in 3.x al vast, Werkzeug is nieuw in die lijst en op Over.
- **Een vaste User-Agent voor bronnen zonder eigen User-Agent.** De productfoto's, de verkenner van AH, de AI-aanvragen, de import van maaltijdboxen en de afbeeldingen voor Mealie stuurden de standaard van Requests (`python-requests/2.33.0`). Nu sturen ze die van Chrome 157 op Android (stabiel sinds 2026-11-03, opgezocht op 2026-11-06), `DEFAULT_USER_AGENT` in `mealieshopper/http_client.py`. AH, Jumbo, Picnic, Crisp, Mealie, HelloFresh, Marley Spoon, de afbeeldingencache en de updatecheck houden hun eigen User-Agent letterlijk. Een test zorgt dat de standaard van httpx (`python-httpx`) nooit de deur uit gaat.
- **De fabrieksreset herstart via het herstartbeleid.** Na het wissen stopt de app zichzelf (SIGTERM) en start het herstartbeleid hem weer: `unless-stopped` of `always` in compose, de watchdog bij de Home Assistant-add-on. De app herkent de container aan `/.dockerenv` en de add-on aan `/data/options.json`, 3.x herkende dat aan Gunicorn. Onder Podman of buiten een container meldt de app dat je hem zelf opnieuw start. Hypercorn draait met `--workers 0`: de app is zelf het proces dat SIGTERM krijgt, dus `after_serving` loopt altijd, en een crash van de app geeft een echte exitcode in plaats van 0. Na het afsluiten staat in het logboek `MealieShopper afgesloten: achtergrondtaken gestopt en verbindingen gesloten`. Lopende verzoeken krijgen bij het afsluiten 3 seconden, de achtergrondtaken daarna 5, binnen de 10 seconden die `docker stop` geeft.
- **Gunicorn-instellingen tellen niet meer.** De container draait een Hypercorn-proces en leest `GUNICORN_CMD_ARGS` en `WEB_CONCURRENCY` niet meer. Staat er in je compose-bestand een eigen `command: gunicorn ...`, haal die dan weg of vervang hem door `hypercorn --bind 0.0.0.0:8000 --workers 0 run:app`. Gunicorn zit niet meer in de image, dus met het oude commando start de container niet.
- **Grenzen op een aanvraag.** Quart begrenst een aanvraag standaard op 16 MiB en 60 seconden, Flask deed dat niet. MealieShopper houdt 16 MiB aan voor het lichaam van een aanvraag. Alleen het klaarzetten van een back-up of van een prijsarchief in één verzoek mag tot de grens van zo'n bestand (200 MB, `backup.MAX_STAGE_BYTES` en `price_archive.MAX_UPLOAD_BYTES`) plus 1 MB, en de prijsimport tot 32 MiB (5 miljoen tekens tekst, `price_import.MAX_TEXT_CHARS`, als JSON). Een groter lichaam krijgt 413, ook bij inloggen, `/oauth/token` en `/mcp`, waar eerst 201 MB doorkwam. `BODY_TIMEOUT` en `RESPONSE_TIMEOUT` staan op 120 seconden. Dat zijn grenzen van Quart op het lezen van het lichaam en het versturen van het antwoord. De `--timeout 120` van Gunicorn in 3.x was iets anders: de tijd waarin een worker een teken van leven moest geven. Een back-up of de export van het prijsarchief downloaden heeft geen tijdslimiet, en bij het uploaden in één verzoek telt alleen de stilte: komt er 120 seconden niets binnen, dan volgt een 408. Een stuk van een upload in stukken (4 MB) blijft onder de 16 MiB en krijgt, zoals in 3.22, een 408 na 30 seconden stilte. Zo stopt een groot bestand over een trage verbinding niet halverwege. Een back-up van 3.x terugzetten werkt dus als voorheen. Statische bestanden vraagt de browser elke keer opnieuw na (ETag, 304), zoals met Flask, in plaats van ze 12 uur te bewaren zoals Quart standaard doet.
- **De AH-loginproxy stuurt alleen door wat AH nodig heeft.** Geen eigen cookies van MealieShopper meer (`ms_session`, `ms_lang`), geen `X-Forwarded-*`, `Forwarded` of `X-Real-IP` en geen `Authorization` naar login.ah.nl. De cookies van AH zelf gaan mee zoals voorheen. Besluit van de eigenaar. Stuurt de browser zijn cookies in meer dan één Cookie-header (zoals over HTTP/2), dan komen ze allemaal bij AH aan. Omgekeerd gaan de hop-by-hop-headers van AH (`Connection`, `Keep-Alive`, `Transfer-Encoding`, `Upgrade` en de rest uit RFC 9110) en `Date` en `Server` niet naar de browser: die zet Hypercorn zelf, en Gunicorn liet ze in 3.x ook weg.
- **Auditlog.** In een foutmelding met `Authorization: Bearer <token>` (of `Basic`, `Token`) staat nu ook het token zelf verborgen, 3.x verving alleen het woord. Een verzoek dat mislukt zonder antwoord (geen verbinding, een time-out) staat niet meer in het auditlog, alleen in het logboek van de app.
- **Een vernieuwd token gaat niet meer verloren.** Het vernieuwen en bewaren van het token van AH en Jumbo, en het bewaren van een nieuwe sleutel van Picnic, loopt door als het verzoek dat erom vroeg wegvalt, bijvoorbeeld omdat de browser sluit. Zo liep een Picnic-verzoek met threads ook door. Dat geldt ook voor *Controleren* bij AH en voor een geplakt refresh-token bij het koppelen. *Controleren* wacht op hetzelfde slot als het vernieuwen, dus twee gelijktijdige vernieuwingen sturen niet hetzelfde oude token. Het bewaart ook het gebruikerstoken dat AH teruggeeft, zodat het volgende verzoek een vernieuwing minder doet. Een geplakt refresh-token doet dat nu ook: is het van een ander AH-account, dan werkt het volgende verzoek niet meer met het gebruikerstoken van het vorige account. Lukt het bewaren toch niet, dan staat er een waarschuwing in het logboek.
- **Een fout in een reeks zoekopdrachten stopt de rest.** Mislukt een zoekopdracht in de winkelpreview, de bonus van AH, de aanbiedingen van Jumbo of de productgegevens van een Jumbo-lijstje buiten zijn eigen foutafhandeling, dan worden de andere opdrachten geannuleerd (`tasks.map_bounded`). De pool van threads liet ze eerst afmaken, terwijl het antwoord toch een fout was.
- **Inloggen en OAuth.** De blokkade na mislukte pogingen, de controle tegen een hergebruikte TOTP-code en het tellen van een mislukte poging lopen achter een slot, ook voor pogingen die tegelijk binnenkomen. Een OAuth-code of refresh-token is maar een keer in te wisselen, ook bij twee gelijktijdige verzoeken.
- **De updatecheck** vraagt GitHub een keer als twee controles tegelijk lopen, met dezelfde meldingen als voorheen.
- **HTML-import.** Een receptpagina zonder charset in de `Content-Type` leest de app nu als UTF-8, Requests las ISO-8859-1. Een pagina die echt Latin-1 is zonder dat te zeggen, kan daardoor verkeerde tekens geven. Cookies die een site bij een doorverwijzing zet, gaan zoals voorheen mee naar de volgende stap.
- **Marley Spoon.** Een doorverwijzing naar een andere host geeft nu de melding dat Marley Spoon ons ergens anders heen stuurde. 3.x gaf daar "status 302".
- **Verkenners.** Een netwerkfout in een rapport van de verkenner heet nu naar het type van httpx (bijvoorbeeld `ConnectError`), niet meer naar dat van Requests.
- **Proxy en eigen CA: ongewijzigd, nu in de README.** `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY`, `REQUESTS_CA_BUNDLE` en `CURL_CA_BUNDLE` werken zoals in 3.x, voor al het uitgaande verkeer. Drie verschillen blijven: `NO_PROXY` met een IP-bereik (`192.168.0.0/16`) telt bij httpx niet (gebruik hostnamen of losse IP-adressen), `.netrc` wordt niet meer gelezen, en `SSL_CERT_FILE` en `SSL_CERT_DIR` gelden nu als `REQUESTS_CA_BUNDLE` en `CURL_CA_BUNDLE` leeg zijn (3.x negeerde ze).
- **Bewuste afwijkingen in routes en JSON.** `items[].error` in de winkelpreview (`/api/ah/cart/preview` en die van de andere winkels) staat nu in de taal van de gebruiker, het was altijd Nederlands. Een stuurteken in het pad van de AH-loginproxy geeft een 502 met de reden, waar Requests het verzoek verstuurde. De afbeeldingencache weigert een paar misvormde adressen die de oude controle doorliet: `%23` in de host, poort 0 en een kapot A-label. Een upload in stukken met een JSON-lichaam dat geen object is (`POST /api/backup/uploads`, `/api/price-archive/uploads` en hun `<token>/stage`) telt als een lichaam zonder `size`: 400 `upload_empty` bij het beginnen, 409 `upload_incomplete` bij het afmaken, waar 3.22 een 500 gaf. Verder zijn de routes en de JSON gelijk aan 3.22.0.
- **Geen meting voor en na.** Besluit van de eigenaar: een tweede installatie met het echte AH-account naast productie is het risico niet waard. Ter vergelijking voor later de cijfers van een productie-installatie op 2026-11-06, voor deze versie: 117 MiB RSS in rust, een piek van 223 MiB sinds de start, 6 threads in de worker, 0,01% CPU, geen OOM of herstart in 72 uur.
- **Ontwikkeling**: pytest (met pytest-asyncio en respx) draait de tests: `MEALIESHOPPER_TESTING=1 python -m pytest`. De nieuwe workflow `.github/workflows/tests.yml` draait de suite op elke pull request naar `beta` en `main` en op elke push naar `beta`, met Python 3.12. De tests kunnen het netwerk niet op, alleen loopback. `tests/test_asyncio_guards.py` bewaakt dat geen module Requests, threads of blokkerend werk in een `async def` gebruikt, dat elke httpx-client via `http_client` komt met een eigen User-Agent, dat een verzoek geen thread achterlaat en dat het afsluiten de taken stopt en de clients sluit. Quart staat op 0.22.0, want 0.23 vraagt Python 3.13 en de image draait op `python:3.12-slim`. `python run.py` start zonder reloader, zoals Flask in 3.x (Quart start er standaard een).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#275](https://github.com/helmerzNL/MealieShopper/pull/275).

## v3.22.0

### Nieuw in 3.22.0

*English summary: new under Settings, System: Export price archive. It downloads all offers and prices as a ZIP with one file per store and month (`offers/<store>/<yyyy-mm>.jsonl`, `prices/<store>/<yyyy-mm>.jsonl`), plus `mealieshopper-price-archive.json` (the header, with the list of files) and a `README.md`, to publish for example in a GitHub repository. Personal offers (Bonus Box) are never included. The same data gives exactly the same files, so git only shows what changed. Import price archive reads the export back, also as the ZIP of a GitHub repository; it now also accepts offers without an address (Picnic, Crisp) and regular prices (`search`, stored as `archive_search`, which Replace removes again).*

- **Nieuw: Prijsarchief exporteren** (Instellingen, Systeem, alleen de beheerder). Je downloadt alle aanbiedingen en prijzen als ZIP, om te delen in bijvoorbeeld een openbare GitHub-repo: pak de ZIP uit in de repo en commit.
  - **Wat erin staat:** alle aanbiedingen van Albert Heijn, Jumbo, Picnic en Crisp, en alle prijzen: de gewone prijzen die de app bij het zoeken zag en wat eerder uit een prijsarchief kwam. Een afbeelding gaat alleen als adres mee.
  - **Nooit erin:** je persoonlijke aanbiedingen (Bonus Box). Bestellingen, kassabonnen, lijstjes en instellingen staan in andere tabellen en gaan dus ook niet mee. Een regel die de import zou weigeren, blijft eruit; de melding na het exporteren noemt hoeveel.
  - **Een bestand per winkel en maand:** `offers/<winkel>/<jjjj-mm>.jsonl` en `prices/<winkel>/<jjjj-mm>.jsonl`, met één regel van het eigen formaat per regel. `mealieshopper-price-archive.json` is de kop, met `files` (de bestanden) en `counts` (het aantal per winkel), en `README.md` zegt wat erin staat, in het Nederlands en het Engels.
  - **Git-vriendelijk:** dezelfde gegevens geven precies dezelfde bestanden, met de regels in een vaste volgorde. Een nieuwe export laat in git dus alleen zien wat er veranderde, meestal de laatste maand. Geen bestand komt in de buurt van de grens van 100 MB van GitHub.
- **De import leest de export terug**, ook als de ZIP van een GitHub-repo (**Code, Download ZIP**), waarin alles in één map staat. Hij leest alleen de bestanden in `files`: een oud bestand of een `LICENSE` in de repo stoort niet. Een bestand dat in `files` staat maar ontbreekt, of een pad buiten de map, weigert het hele archief.
- **Gewijzigd in de import:**
  - Een aanbieding zonder adres mag nu: Picnic en Crisp hebben geen webwinkel. Zo'n aanbieding staat er al als hetzelfde product met dezelfde looptijd er staat.
  - Een gewone prijs (`search`, wat een MealieShopper bij het zoeken zag) mag nu ook, en wordt `archive_search`. Hij telt als gewone prijs, telt niet mee als product van jou bij het koppelen, en **Vervang wat eerder uit een prijsarchief kwam** haalt hem er weer uit.
- Met de database van de vorige proef (45.048 aanbiedingen en 44.170 prijzen) maakt de app de export in ruim 2 seconden: 437 bestanden, 5,1 MB als ZIP. Twee exporten na elkaar geven dezelfde bestanden. Terug geïmporteerd in een lege installatie, ook als ZIP van een repo met een map erboven, komen alle prijzen en 45.019 aanbiedingen binnen: de 29 andere zijn combi-aanbiedingen uit een import van vóór 3.21.0, die de import nu tegenhoudt.
- **API:** `POST /api/price-archive/export` geeft de ZIP, met `X-Export-Offers`, `X-Export-Prices` en `X-Export-Skipped`. Na het exporteren staat een regel in het auditlog (`price-archive`, `export`).
- **Handleiding** (NL en EN): nieuw onderdeel *Prijsarchief exporteren*; bij *Het eigen formaat* staan de map, de aanbieding zonder adres en de bronnen `search` en `archive_search`. De README's noemen de export.
- **Ontwikkeling**: nieuw `price_archive_export.py`. `test_price_archive` heeft 6 nieuwe tests: een bestand per winkel en maand zonder persoonlijke aanbiedingen (met de README en het auditlog), twee keer dezelfde bestanden, terug importeren (ook nog een keer), de ZIP van een GitHub-repo, een ontbrekend bestand, een kapotte regel of een pad buiten de map, en een regel die de import zou weigeren. De export-route staat in de toegangstests, en de bron `search` is niet meer het voorbeeld van een geweigerde bron (dat is nu `receipt`).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-14`). Ververs de pagina één keer.
- Pull request [#273](https://github.com/helmerzNL/MealieShopper/pull/273).

## v3.21.0

### Gewijzigd in 3.21.0

*English summary: a price archive now only adds leaflet lines that certainly belong to one product. After the tidying in 3.18.0 tens of thousands of "Folder: ..." products were still left (toys, decorations, products you never buy), and real products showed long leaflet texts with "koppeling onzeker". A leaflet line now only comes in when it is linked exactly (same name and pack size) to a product the app knows, with the linking from wolph's `import_archive.py`, or when the archive itself already linked it exactly. Combination offers and uncertain links never come in, and only the deal remains as promotion text. Prices from the archive (Picnic, Crisp) follow the same rule: only those that belong exactly to a product of yours come in, with that product, so the "Archief: ..." products disappear too. Import the package again with Replace what came from a price archive before ticked to clear out the old lines. For an archive of your own, `?keep_unlinked=1` keeps unlinked lines and prices and uncertain links.*

- **Alleen wat zeker bij één product hoort.** Na het opschonen uit 3.18.0 bleven er nog tienduizenden *Folder: …*-producten over: speelgoed, versiering, en producten die je nooit koopt (*AH Excellent tafelversiering*, *A-merk speelgoed*). En bij echte producten stond een lange foldertekst met *koppeling onzeker*, omdat een regel op woorden aan een product was gekoppeld, vaak een andere variant. Een folderregel komt er nu alleen in als hij zeker bij één product hoort:
  - **Exact gekoppeld**: dezelfde naam en verpakking als een product dat de app kent (je aanbiedingen, bijgehouden prijzen en bestellingen), met de koppeling uit `import_archive.py` van wolph, of al exact gekoppeld door het archief. Op woorden koppelt de app niet meer.
  - **Nooit een combi-aanbieding** (*A of B*, *alle …*, *combineren mogelijk*, en nu ook *naar keuze*), ook niet als het archief hem al aan een product koppelde.
  - **Geen onzekere koppeling**: een regel die het archief zelf op woorden koppelde, blijft weg.
  - **Korte actietekst**: alleen de actie (*2 voor: 1.00 Los*), zonder *Folder |*, *bron: …* en *koppeling onzeker: …*. De app leest daarin geen actie, want de prijs uit het archief is al per stuk.
- **Ook de prijzen van Picnic en Crisp.** De *Archief: …*-producten verdwijnen net zo: een prijs uit een archief zonder product (`folder:…`) komt er alleen in als hij exact bij een Picnic- of Crisp-product van jou hoort, en staat dan bij dat product. Het pakket koppelt die prijzen zelf aan geen enkel product, want de product-id's van Picnic veranderden in april 2023.
- **Dezelfde verpakking** is dezelfde hoeveelheid, of bij een multipack (*6 x 300 ml*) letterlijk dezelfde tekst. Een multipack koppelde eerst nooit.
- **Importeer het pakket opnieuw** met **Vervang wat eerder uit een prijsarchief kwam**: dan staat er geen enkel *Folder: …*- of *Archief: …*-product meer en verdwijnen de lange folderteksten. Met het echte pakket en een database met de vorige import (24.604 *Folder: …*-producten) komen er 656 aanbiedingen bij een echt product binnen; 131.898 folderregels blijven buiten (89.248 combi-aanbiedingen, 1.984 onzekere koppelingen en 40.666 regels zonder product). Die database heeft geen Picnic- of Crisp-producten, dus de 44.170 prijzen blijven ook buiten; met drie Picnic-producten die in het archief staan, kwamen precies hun 72 prijzen bij die producten binnen. Koop of zoek je later producten die in het archief staan, importeer dan nog een keer met Vervangen.
- **Voor een eigen archief** houdt `?keep_unlinked=1` bij het uploaden (`POST /api/price-archive/stage` en `POST /api/price-archive/uploads/<token>/stage`) de ongekoppelde regels en prijzen als eigen product en de onzekere koppelingen bij hun product. Combi-aanbiedingen komen er ook dan niet in, en een ongekoppelde folderregel zonder gewicht, inhoud of aantal krijgt geen prijs.
- **Ook een overgeslagen regel wordt gecontroleerd**: een kapotte regel weigert nog steeds het hele bestand. De samenvatting zegt in één zin hoeveel regels zeker bij één product horen en hoeveel er zijn overgeslagen (`tidied` met `exact`, `linkedByArchive`, `groups`, `uncertain` en `unlinked`), en in een tweede zin hetzelfde voor de prijzen (`pricesExact` en `pricesUnlinked`).
- **Verwijderd:** de koppeling op woorden bij het importeren, en daarmee `tidied.approximate` in de samenvatting.
- **Auditlog eerst.** De regel in het auditlog staat er nu voordat de import als klaar wordt gemeld.
- **Handleiding** (NL en EN): *Alleen wat zeker bij één product hoort* (met de prijzen uit een archief) en *Zo zie je ze*.
- **Ontwikkeling**: `test_price_archive` heeft 9 nieuwe tests: standaard alleen regels en prijzen die zeker bij een product horen (in één keer en in stukken geüpload), een prijs met een andere naam of verpakking, een multipack met dezelfde tekst, een onzekere koppeling uit het archief, een kapotte regel die ook overgeslagen zou worden, de optie `keep_unlinked`, en de korte actietekst (alleen de actie, en de app leest er geen actie in). De bestaande tests uploaden met `keep_unlinked` en volgen de nieuwe regels.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-13`). Ververs de pagina één keer.
- Pull request [#272](https://github.com/helmerzNL/MealieShopper/pull/272).

## v3.20.1

### Opgelost en bijgewerkt in 3.20.1

*English summary: restoring a backup with login switched off no longer loses your own profile (theme, accent colour, navigation bar, cooking mode, Prefer organic and the preferred supermarket); it used to be skipped as "a user that does not exist here". The screenshots of Settings now show the menu Preferences, with a new one of the Supermarket block in the manual.*

- **Opgelost: zonder inloggen raakte terugzetten je eigen profiel kwijt.** Met inloggen uit (`PASSKEY_AUTH_ENABLED=false`) doet de app alles als de beheerder, maar die staat dan niet als gebruiker in de database. Het terugzetten sloeg zijn profiel daarom over als *profiel van een gebruiker die hier niet bestaat*, en het profiel dat er stond was dan al weg: thema, accentkleur, navigatiebalk, kookmodus, *Liever biologisch* en de voorkeurssupermarkt gingen terug naar de standaard. Het profiel van de beheerder hoort nu altijd bij de installatie; dat van een gebruiker die hier niet bestaat, wordt nog steeds overgeslagen.
- **Schermafbeeldingen bijgewerkt** (NL en EN): Instellingen met het menu **Voorkeuren** (`appearance`, `navbar`, `users`, `audit`, `audit-detail` en `updates`), en een nieuwe van het blok **Supermarkt** met Jumbo als voorkeurssupermarkt (`preferences`, in de handleiding onder *Voorkeuren*). Het scenario `appearance` klikte nog op het blok van voor 3.20.0 en liep vast.
- **Ontwikkeling**: `test_backup` heeft 2 nieuwe tests (het profiel van de beheerder zonder inloggen, en een profiel van iemand die er niet is).
- Pull request [#271](https://github.com/helmerzNL/MealieShopper/pull/271).

## v3.20.0

### Nieuw en gewijzigd in 3.20.0

*English summary: under Settings the menu Appearance is now called Preferences, and its block Preferences (cooking mode) is now called Appearance. A new block Supermarket has the preferred supermarket: Albert Heijn (the default), Jumbo, Picnic or Crisp. Offers and Stores open with it, per user, as soon as that store is linked; until then they open with Albert Heijn. A store you pick yourself on one of those pages stays while the app is open, and a new preferred supermarket applies to both pages again at once.*

- **Nieuw: de voorkeurssupermarkt** (Instellingen, Voorkeuren, blok **Supermarkt**). Kies *Albert Heijn* (de standaard), *Jumbo*, *Picnic* of *Crisp*; **Aanbiedingen** en **Winkels** openen daarmee. Het hoort bij je account, dus elke huisgenoot kiest zelf en op een ander apparaat zie je hetzelfde.
  - Is de gekozen winkel (nog) niet gekoppeld, dan staat dat onder de keuze en openen beide pagina's met Albert Heijn tot je hem koppelt.
  - Kies je op Aanbiedingen of Winkels zelf een andere winkel in het uitklapmenu, dan blijft die staan zolang de app open is. Een nieuwe voorkeurssupermarkt geldt meteen weer voor beide pagina's.
  - Aanbiedingen wacht bij het openen even op de koppeling van je voorkeurssupermarkt, in plaats van eerst de aanbiedingen van Albert Heijn te laden en dan te wisselen. Winkels wisselt alleen terwijl je hem open hebt, zodat er op de achtergrond niets bij een winkel wordt opgehaald.
  - Het weekmenu en het Mandje onthouden zoals voorheen hun eigen laatste keuze.
- **Gewijzigd: het menu Weergave heet nu Voorkeuren**, en het blok *Voorkeuren* daarin (de kookmodus) heet nu **Weergave**. De blokken zijn nu *Supermarkt*, *Weergave* en *Thema*. Het adres is `/settings/preferences`; `/settings/appearance` opent de pagina nog.
- **Adressen van Aanbiedingen.** `/offers` zonder winkel is nu je voorkeurssupermarkt (eerst was dat altijd Albert Heijn). Kies je een andere winkel, dan staat die in het adres, ook Albert Heijn (`/offers?store=ah`) als je voorkeur een andere winkel is. Een link naar de prijsgeschiedenis vanuit een AH-bestelling of -kassabon opent dus altijd Albert Heijn.
- **Verwijderd:** de onthouden winkel van Aanbiedingen in de browser (`ms-offers-store`). Die werd nooit gebruikt, omdat de pagina altijd al bij Albert Heijn begon; de voorkeurssupermarkt neemt die rol over.
- **API:** `PUT /api/profile/preferences` neemt `preferredStore` (`ah`, `jumbo`, `picnic` of `crisp`; iets anders geeft `400`), en `GET /api/auth/status` en `GET /api/profile` geven hem terug. Nieuwe kolom `preferred_store` in `user_profiles` (standaard `ah`); hij gaat mee in de back-up, en een oudere back-up zet Albert Heijn terug.
- **Handleiding** (NL en EN): het onderdeel *Weergave* heet nu *Voorkeuren*, met het blok Supermarkt; bij Aanbiedingen en Winkels staat waarmee ze openen. De README's noemen de voorkeurssupermarkt.
- **Ontwikkeling**: nieuw `test_preferred_store` (14 tests: de standaard en terugvallen op AH, adressen met en zonder winkel, volgen tot je zelf kiest, een dichte pagina, een adres dat op een koppeling wacht, Winkels alleen als hij open is, een nieuwe voorkeur na een eigen keuze, de namen en het bewaren). `test_profile` test de nieuwe voorkeur (standaard, keuzes, weigeren, per gebruiker, een oude database), `test_backup` neemt hem mee, en `test_appearance`, `test_router` en `test_offers` volgen de nieuwe namen.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-12`; 3.18.2 en 3.19.0 gebruikten al `20261106-10` en `20261106-11`). Ververs de pagina één keer.
- Pull request [#269](https://github.com/helmerzNL/MealieShopper/pull/269).

## v3.19.0

### Nieuw in 3.19.0

*English summary: under Settings, System, Backup and restore you choose what goes along. When making a backup you can leave out the current offers and the offer history (a smaller file); when restoring you can untick the links, the current offers and the offer history. What is not in the file or what you untick stays as it is. Without the links no password is needed, so you can still restore your data if you lost the password of the file.*

- **Bij het maken: de aanbiedingen van nu en de historie van aanbiedingen mogen eruit.** Twee nieuwe vinkjes naast *Neem de koppelingen mee*: *Neem de aanbiedingen van nu mee* en *Neem de historie van aanbiedingen mee*. Beide staan aan. Uitgevinkt gaan die tabellen niet in het bestand (de aanbiedingen van nu: `offers` en `offers_meta`; de historie: `offer_history`), en het bestand zegt welke onderdelen ontbreken (`left_out`). Zo wordt het bestand kleiner.
- **Bij het terugzetten: kiezen wat je terugzet.** Onder *Wat zet je terug?* staan vinkjes voor wat er in het bestand zit:
  - **De koppelingen.** Uitgevinkt is er geen wachtwoord nodig: handig als je het wachtwoord van het bestand kwijt bent. De koppelingen die er nu zijn, blijven staan.
  - **De aanbiedingen van nu** en **de historie van aanbiedingen**, met het aantal.
  - Wat je uitvinkt, en wat niet in het bestand zit, blijft zoals het nu is; de samenvatting zegt *niet in dit bestand* en na afloop *Niet teruggezet, gebleven zoals het was*.
- **Een bewerkt bestand** dat zegt dat een onderdeel ontbreekt terwijl het er wel in staat, of een onbekend onderdeel noemt, wordt geweigerd. Een oudere back-up zonder `left_out` werkt zoals altijd: alles gaat terug.
- **API:** `POST /api/backup` en `POST /api/backup/jobs` kennen `"offers": false` en `"offerHistory": false`; `POST /api/backup/jobs` ook `"links": false` (dan geen wachtwoord). Zonder die velden gaat alles mee, zoals voorheen. De samenvatting heeft `leftOut`, het resultaat van het terugzetten `kept`.
- **README** (NL en EN): wat je kiest bij maken en terugzetten, en wat te doen als je het wachtwoord kwijt bent.
- **Ontwikkeling**: `test_backup` heeft 7 nieuwe tests (weglaten, wat weggelaten is blijft staan, uitvinken bij terugzetten, zonder koppelingen geen wachtwoord, bewerkt bestand, oudere back-up, elk onderdeel heeft een regel op het scherm).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-11`). Ververs de pagina één keer.
- Pull request [#270](https://github.com/helmerzNL/MealieShopper/pull/270).

## v3.18.2

### Opgelost in 3.18.2

*English summary: a backup upload in parts (3.18.1) could freeze the whole app. A part that got stuck on the way kept a lock while the server waited for the rest of it, so every retry waited behind it and took one of the four workers; after a few retries nothing answered any more, and each next attempt hung earlier. The server now reads a part completely before taking the lock, gives up a part that stays silent for 30 seconds (408, with a line in the log), and the screen sends a part again after 30 seconds without progress instead of two minutes.*

- **Opgelost: een tweede of derde poging om een back-up terug te zetten bleef steeds eerder hangen** (na 20 MB, daarna al na 4 MB), en dan antwoordde de hele app niet meer. Bleef één stuk onderweg steken, dan hield de server een slot vast terwijl hij op de rest van dat stuk wachtte. Elke nieuwe poging wachtte achter dat slot en nam een van de vier werkers van de app in beslag; na een paar pogingen stond alles stil, tot een herstart. Dat zat in 3.18.1.
  - De server leest een stuk nu eerst helemaal en houdt het slot alleen vast tijdens het schrijven (milliseconden). Een stuk dat blijft steken, houdt de andere niet meer op.
  - Een stuk waar 30 seconden niets van binnenkomt, geeft de server op met `408` (en een regel in het logboek: *Upload …: stuk afgebroken na … bytes*). Gunicorn heeft zelf geen tijdslimiet voor het lezen van een verzoek, dus zo'n werker bleef anders voor altijd bezet.
  - Het scherm stuurt een stuk opnieuw als er 30 seconden niets mee gebeurt (was: twee minuten) en probeert het ook bij `408` opnieuw.
- **Ontwikkeling**: `test_backup` heeft 2 nieuwe tests: een stuk dat blijft steken, houdt een ander stuk niet op (op 3.18.1 wachtte dat 10 seconden), en een verbinding die stil blijft, geeft `408` en zet de tijdslimiet van de verbinding daarna terug. Nagebootst met gunicorn en een proxy die twee stukken halverwege laat steken: de upload ging na 2 × 30 seconden verder, `/healthz` bleef antwoorden, en de twee pogingen daarna duurden elk 8 seconden.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-10`). Ververs de pagina één keer.
- Pull request [#268](https://github.com/helmerzNL/MealieShopper/pull/268).

## v3.18.1

### Opgelost in 3.18.1

*English summary: restoring a large backup (or importing a large price archive) no longer hangs during the upload. The screen now sends the file in parts of 4 MB, each a request of its own, so a reverse proxy, tunnel or CDN in front of the app (Cloudflare's free plan allows at most 100 MB per request) no longer refuses it or leaves it waiting; a part that fails is sent again, and an upload that really gets no further now says so.*

- **Opgelost: het terugzetten van een grote back-up bleef hangen tijdens het uploaden** (bijvoorbeeld op *9,6 MB van 100,5 MB*). Met een geïmporteerd prijsarchief is een back-up al snel ruim 100 MB, en het scherm stuurde dat in één verzoek. Wat voor de app staat (een reverse proxy, een tunnel of CDN zoals Cloudflare, dat op de gratis versie hoogstens 100 MB per verzoek doorlaat) weigerde dat of gaf het nooit door; de browser bleef dan staan bij wat in de buffers paste en hoorde nooit waarom. De app zelf verwerkt zo'n bestand prima (in een test 115 MB in 8 seconden, uploaden en controleren samen).
- **Uploaden in stukken** (nieuw: `uploads.py` en `static/upload.js`). Het scherm stuurt het bestand in stukken van 4 MB, elk een eigen verzoek, en de server zet ze achter elkaar in `.restore-<code>.part` in de data-map; daarna controleert hij het bestand zoals altijd. De balk loopt over het hele bestand.
  - Een stuk dat mislukt, twee minuten geen antwoord krijgt of een `502`, `503` of `504` van de proxy krijgt (of de varianten van Cloudflare), gaat tot vier keer opnieuw. De server zegt bij elk stuk hoeveel hij al heeft (`409` met `received` als het niet aansluit), dus na een verloren antwoord gaat het verder waar de server is, zonder iets dubbel te plakken.
  - Weigert een proxy zelfs een stuk van 4 MB (`413`), dan worden de stukken kleiner (tot 256 kB).
  - Komt de upload echt niet verder, dan zegt het scherm dat, in plaats van te blijven staan.
  - Een te groot bestand (boven 200 MB) wordt meteen geweigerd, vóór de eerste byte. Een upload waar een uur niets bij kwam, wordt opgeruimd; een fabrieksreset neemt hem mee.
- **Het prijsarchief** (Instellingen, Systeem, Prijsarchief importeren) uploadt op dezelfde manier; het archief van de gist is ook ruim 100 MB.
- **API:** `POST /api/backup/uploads` met `{"size": …}`, `PUT /api/backup/uploads/<token>?offset=…` (het stuk als body), `DELETE /api/backup/uploads/<token>` en `POST /api/backup/uploads/<token>/stage` met `{"size": …}`; hetzelfde onder `/api/price-archive/uploads`. `POST /api/backup/stage` en `POST /api/price-archive/stage` met het hele bestand werken nog zoals voorheen.
- **README en handleiding** (NL en EN): een proxy hoeft geen verzoek van 200 MB meer toe te laten; de regel over de 120 seconden van gunicorn is weg.
- **Ontwikkeling**: `test_backup` heeft 11 nieuwe tests voor uploaden in stukken (dezelfde samenvatting en het terugzetten, een stuk dat niet aansluit, te groot vooraf en per stuk, de grens, een onvolledige upload, een geweigerd bestand, tokens, weggooien, opruimen, rechten en fabrieksreset) en `test_price_archive` 2; de routes staan in de toegangstests.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-9`). Ververs de pagina één keer.
- Pull request [#267](https://github.com/helmerzNL/MealieShopper/pull/267).

## v3.18.0

### Nieuw, opgelost en verwijderd in 3.18.0

*English summary: importing a price archive no longer fills the Price history with odd products. Every leaflet line is tidied up on import: it is linked to your own products where possible (with the linking from wolph's `import_archive.py`), a combination offer ("A or B", "all ...") that belongs to no product is left out, and a leaflet line without a known pack size gets no price, so prices of different packs no longer share one chart. Upload the package again with Replace what came from a price archive before ticked, and the earlier import is replaced by the tidied one. No separate tool is needed any more.*

- **Opgelost: vreemde producten in de Prijsgeschiedenis.** Na de import van het folderarchief stond bij een zoekopdracht als *runderhamburger* een lange rij *Folder: …*-producten. Daarbij zaten combi-aanbiedingen (*AH Runderhamburger 2 stuks of gehaktschnitzel …*, ruim 60% van het archief), en folderproducten zonder verpakking waarin prijzen van verschillende schalen door elkaar liepen (€2,25 en €0,93 bij *Folder: AH Runderhamburgers*). Upload het pakket opnieuw met **Vervang wat eerder uit een prijsarchief kwam** aangevinkt: bij dat voorbeeld zijn het er dan 9 in plaats van 46.
- **Opschonen bij het importeren** (nieuw: `price_archive_tidy.py`). Elke folderregel (een aanbieding met een product-id `folder:…`) gaat eerst hierdoor:
  - **Koppelen aan je eigen producten:** de app zoekt hem op tussen wat hij zelf kent (je aanbiedingen, bijgehouden prijzen en bestellingen; geen archiefregels). Eerst exact op naam en verpakking, anders op woorden; dan zonder prijs, omdat verpakking of variant niet vaststaat. De koppeling komt uit `import_archive.py` van wolph (`match_product` en `link_approximate`, met dezelfde drempels).
  - **Combi-aanbiedingen die bij geen product horen, komen er niet in.**
  - **Een folderregel zonder gewicht, inhoud of aantal krijgt geen prijs**; hij telt wel als keer in de aanbieding.
  - De samenvatting zegt hoeveel regels zijn gekoppeld, overgeslagen en zonder prijs gebleven (`tidied`). Een regel die het archief zelf al aan een product koppelde, blijft zoals hij is, en een regel die het opschonen niet kan lezen gaat ongemoeid naar de controle.
- **Verwijderd: het losse hulpscript om het archief om te zetten.** Het opschonen zit nu in de import zelf, dus je uploadt gewoon het pakket. De handleiding beschrijft het niet meer.
- **Handleiding** (NL en EN): het opschonen bij het importeren, en hoe je met Vervangen een eerdere import vervangt.
- **Ontwikkeling**: `test_price_archive` heeft 6 nieuwe tests voor het opschonen (combi's, verpakking, koppelen aan eigen producten, een regel die het archief al koppelde, het eigen formaat, onleesbare regels). De nepmap in de tests heeft een verzonnen naam.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION` wordt `20261106-8`; 3.16.1 en 3.17.0 gebruikten allebei `20261106-7`, waardoor een browser die 3.16.1 al had de stijlen en teksten van 3.17.0 niet ophaalde). Ververs de pagina één keer.
- Pull request [#266](https://github.com/helmerzNL/MealieShopper/pull/266).

## v3.17.0

### Nieuw in 3.17.0

*English summary: under Settings, System the price archive import has a new box, Replace what came from a price archive before, that removes earlier archive lines first, in the same transaction; what the app fetched itself stays.*

- **Vervang wat eerder uit een prijsarchief kwam** (Instellingen, Systeem, Prijsarchief importeren). Staat er al iets uit een archief, dan toont de samenvatting een vinkje met hoeveel aanbiedingen en prijzen dat zijn. Aangevinkt gaan die er eerst uit, in dezelfde transactie als de import, en de samenvatting rekent dan uit wat er na het vervangen bij komt. Een archiefregel is een aanbieding met een product-id `folder:…` of een adres van supermarktaanbiedingen.com of voordeelmuis.nl, of een prijs met de bron `public_press_release` of `public_observed_unknown`; wat de app zelf ophaalde blijft altijd staan. `POST /api/price-archive/jobs` neemt `replace: true`; de samenvatting van de upload geeft `archived` en `replace`, de klus `removed`.
- **Teksten.** De inleiding van het vak zegt niet meer dat er nooit iets wordt vervangen, en de regel onderaan volgt het vinkje.
- **Handleiding** (NL en EN): het vinkje Vervangen. De README's noemen het vinkje.
- **Ontwikkeling**: `test_price_archive` heeft 7 nieuwe tests voor het vervangen (alleen archiefregels, zonder vinkje niets weg, alleen `true`, dezelfde rijen bij hetzelfde pakket, correcties, mislukken, een lege installatie).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#263](https://github.com/helmerzNL/MealieShopper/pull/263).

## v3.16.2

### Gewijzigd in 3.16.2

*English summary: MealieShopper's MIT license now names Flux76 as copyright holder (`Copyright (c) 2026 Flux76`), in the `LICENSE` file and in the License section of both READMEs. The license itself and the app are unchanged.*

- **Licentie op naam van Flux76.** In `LICENSE` staat nu `Copyright (c) 2026 Flux76` in plaats van `Helmer`. De licentie zelf blijft MIT, met dezelfde tekst.
- **README's** (NL en EN): de regel onder *Licentie* / *License* luidt nu `MIT © 2026 Flux76`, gelijk aan het `LICENSE`-bestand.
- Aan de app zelf verandert niets; je hoeft de pagina niet te verversen.
- Pull request [#265](https://github.com/helmerzNL/MealieShopper/pull/265).

## v3.16.1

### Gewijzigd in 3.16.1

*English summary: the blocks under Settings, Link now have the same compact format as the blocks under Appearance: a lower heading while closed, 18 px titles and 16 px subheadings. The Offers block folds open and shut as well (open by default), and AI review of matches is closed until you open it, also without a key. Reload the page once after updating.*

- **Koppelen in het formaat van Weergave.** De inklapbare blokken onder Instellingen, Koppelen (**AI-beoordeling van koppelingen**, **Mealie**, **Albert Heijn**, **Jumbo**, **Picnic** en **Crisp**) gebruiken nu ook `.fold--compact`: dicht alleen hun kop, met 8 px boven en onder de knop in plaats van 24 px, een kop van 18 px en minder ruimte tussen de blokken. Open zijn de tussenkoppen (zoals *Koppeling afronden* en *Jumbo-routes verkennen*) 16 px, net als *Accentkleur* onder Weergave; ze waren groter dan de kop van het blok zelf.
- **Aanbiedingen inklapbaar.** Het blok **Aanbiedingen** (synchroniseren, de stand per winkel en de planning) klapt nu ook open en dicht. Het staat standaard open.
- **AI-beoordeling standaard dicht.** Het blok staat dicht tot je het opent, ook als er nog geen sleutel is (eerst stond het dan open). Een melding na opslaan of testen blijft zichtbaar onder de kop.
- Wat je zelf open- of dichtklapt onder Koppelen blijft zo tot je de pagina herlaadt, zoals eerst.
- **Handleiding** (NL en EN) en de huisstijl: de blokken onder Koppelen en hun standaardstand. Nieuwe screenshots `settings-stores.png`, `settings-picnic.png`, `settings-jumbo.png` en `settings-crisp.png`.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#262](https://github.com/helmerzNL/MealieShopper/pull/262).

## v3.16.0

### Nieuw in 3.16.0

*English summary: under Settings, System the administrator can now import a price archive: historical leaflet and store prices that go into the Price history, such as wolph's archive of Albert Heijn and Jumbo (2009-2026), Picnic (2022-2023) and Crisp (2021-2026). Upload the ZIP exactly as Download ZIP on the gist gives it, its .tar.gz, or a .jsonl file in the new documented `mealieshopper-price-archive` format. The app checks every checksum and line, shows per store what is new, and only adds rows: nothing is replaced, and what the app already has always wins. Prices with an unknown promotion status (the Picnic observations) now appear separately as Observed prices, purple diamonds with their source, and never count as a regular price (wolph's change).*

- **Prijsarchief importeren** (Instellingen, Systeem; alleen de beheerder). Kies een bestand, en MealieShopper controleert het helemaal en toont per winkel hoeveel aanbiedingen en prijzen er nieuw zijn, hoeveel er al staan, wat overgeslagen wordt en welke periode het archief beslaat. Pas met **Importeren** komen ze erbij, op de server in één transactie; de pagina verlaten stopt niets. Anders dan **Back-up terugzetten** wordt er niets vervangen of verwijderd.
- **Twee formaten.** De ZIP zoals **Download ZIP** van de gist hem geeft (`supermarket-manifest.json` met Base64-delen van het `.tar.gz`, daarin `data-manifest.json` en zes gegevensbestanden), of het losse `.tar.gz`. En een nieuw, gedocumenteerd eigen formaat, `mealieshopper-price-archive` versie 1: een `.jsonl` (of `.jsonl.gz`) met een kop en regels van de soort `offer`, `price` of `correction`. De handleiding beschrijft het, met een voorbeeld.
- **Wat er al staat gaat voor.** Dubbele regels slaat de import over (zelfde winkel, adres en looptijd, of een aanbieding die de app zelf al kent), net als een prijs op een dag waarop de app al een eigen prijs heeft. De 153 correcties van wolph veranderen alleen een eerder geïmporteerde archiefregel waarvan de oude waarde precies klopt. Nog een keer importeren voegt niets toe.
- **Niets wordt zomaar vertrouwd.** Elke controlesom (delen, archief en gegevensbestanden) en elke regel wordt gecontroleerd: winkel, datums, prijzen, https-adressen, lengtes en de bron van een prijs (alleen `public_press_release` of `public_observed_unknown`, dus nooit een eigen meting). Een fout weigert het hele bestand, met bestand, regelnummer en reden. De tar wordt alleen gelezen, nooit uitgepakt; scripts uit het pakket draaien nooit en afbeeldingsadressen neemt de app niet over. Een stuurteken in vrije tekst wordt een spatie (er staat een ESC in één Jumbo-folder).
- **Snel en zuinig.** Het complete pakket (176.877 regels) controleert de app in een paar seconden. Bij het uploaden zet hij de nieuwe regels klaar, zodat de import zelf de database maar kort vasthoudt (op een testmachine 2,4 s). Het geüploade bestand blijft niet staan.
- **Waargenomen prijzen** (van wolph, uit zijn branch `public-price-observations`). Prijzen met bron `public_observed_unknown` staan in de Prijsgeschiedenis als paarse ruiten en in een eigen tabel *Waargenomen prijzen*, met verpakking en bronlink. Ze tellen niet als gewone prijs, aanbieding of aankoop, en dus ook niet mee in het oordeel *echt voordelig*. `GET /api/prices/history` en de MCP-tool `get_price_history` geven ze apart als `observed`.
- **API.** `GET /api/price-archive`, `POST /api/price-archive/stage`, `DELETE /api/price-archive/stage/<token>`, `POST /api/price-archive/jobs`, `GET /api/price-archive/jobs/latest` en `DELETE /api/price-archive/jobs/<id>`. Na een import staat er een regel in het auditlog.
- **Documentatie**: een nieuwe paragraaf *Prijsarchief importeren* in de handleiding (NL en EN) met beide formaten, en *Waargenomen prijzen* bij Aanbiedingen. De README's noemen het prijsarchief bij de functies, bij *Grootste upload* (200 MB, net als een back-up) en bij *Data en back-ups*.
- **Ontwikkeling**: `test_price_archive` (24 tests) bouwt een nep-gist-ZIP, een `.tar.gz` en het eigen formaat en test de import, het herhalen, wat al bestaat, de correcties en alles wat geweigerd wordt. Wolphs tests voor de waargenomen prijzen komen mee (`test_prices`, `test_backup`, `test_observed_prices_js`).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull requests [#259](https://github.com/helmerzNL/MealieShopper/pull/259) en [#261](https://github.com/helmerzNL/MealieShopper/pull/261).

## v3.15.0

### Nieuw in 3.15.0

*English summary: Settings, Appearance is shorter and grouped into two blocks that fold open and shut like the blocks under Link, and that are lower while closed: Preferences and Theme. Preferences holds a new Cooking mode switch, on by default and stored with your account. Turned off, a recipe no longer shows the bar with Start cooking, the progress and Keep screen on, nor the ticks and the counter next to the ingredients, and the screen does not stay on; the numbered steps stay. Theme holds the accent colour, the theme with the background of the dark theme, and the navigation bar, separated by a line. Reload the page once after updating.*

- **Weergave in twee blokken.** Instellingen, Weergave heeft nu twee inklapbare blokken zoals onder Koppelen: **Voorkeuren** en **Thema**. Dicht is een blok alleen zijn kop, lager dan de blokken van Koppelen (8 px boven en onder de knop in plaats van 24 px); open lopen de onderdelen door. Zolang je ze niet opent, staan ze dicht; deze browser onthoudt welke je open had (`ms-fold-appearance-prefs` en `-theme`). De uitleg bij elk onderdeel is korter.
- **Thema**, met een lijn tussen de onderdelen: *Accentkleur*, *Thema & achtergrond* (licht, donker of het apparaat, met daaronder de achtergrond van het donkere thema) en *Navigatiebalk* (met het glaseffect bij de iOS-achtige balk).
- **Voorkeuren: de kookmodus aan of uit.** Een schakelaar, standaard aan. Uit staat bij een recept geen balk meer met **Start met koken**, de voortgang, **Opnieuw beginnen** en **Scherm aan laten**, en staan er geen vinkjes en geen teller bij de ingrediënten: een tik op een ingrediënt doet dan niets, en het scherm blijft niet aan. De genummerde stappen blijven staan. Dit geldt overal waar een recept opent (Recepten zoeken, Winkels, Maaltijdboxen, het venster van een maaltijd). De keuze staat bij je profiel (nieuwe kolom `cooking_mode` in `user_profiles`, ook in de back-up); `PUT /api/profile/preferences` neemt `cookingMode` (waar of onwaar, iets anders geeft 400). Het script in de kop van de pagina zet hem voor de eerste weergave (klasse `no-cook-mode` op `<html>`), dus de balk flitst niet.
- **Een echte schakelaar.** Nieuw onderdeel `.switch`: een selectievakje met `role="switch"`, getekend als schuifje in de accentkleur, met een rand die 3 : 1 haalt en een zichtbare focusring.
- **Handleiding** (NL en EN): de nieuwe indeling van Weergave en waar je de kookmodus uitzet; de huisstijl beschrijft de compacte blokken en de schakelaar. Nieuwe screenshots `appearance.png` en `navbar.png`.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#260](https://github.com/helmerzNL/MealieShopper/pull/260).

## v3.14.0

### Nieuw in 3.14.0

*English summary: an opened recipe is now shown in a cooking view you can use while you cook, wherever you open it: under Find recipes, under Stores, under Meal kits and in the window of a meal in the week menu. The ingredients sit next to the numbered steps (and stay in view while you scroll on a wide screen), with the amounts in bold. Tick off ingredients and steps; the step you are at is marked Now, and your progress is kept on the device for 12 hours. Start cooking opens a full-screen cooking mode with one step at a time in large type, Previous and Next, arrow keys and swiping, and the ingredients alongside. Keep screen on (off by default, remembered per device) keeps the screen awake with the browser's Screen Wake Lock while a recipe is in view. Headings from Mealie (sections of ingredients, titles of steps) are shown too.*

- **Kookweergave overal waar je een recept opent**: bij Recepten zoeken (Mealie, de winkels en de maaltijdboxen), onder Winkels (AH-recepten, Jumbo, Picnic, Crisp), bij Maaltijdboxen en in het venster van een maaltijd in het weekmenu. De ingrediënten staan naast de genummerde stappen; op een breed vak blijven de ingrediënten in beeld terwijl je door de stappen scrolt, op een telefoon staan ze onder elkaar. Het aantal en de eenheid staan vet (**400 g** spaghetti, **1½ el** olijfolie, **2-3 teentjes** knoflook, **snufje** zout), de stappen in grotere letters.
- **Afvinken en de stap waar je bent.** Vink ingrediënten af als je ze klaar hebt gezet, en tik op het nummer van een stap als die klaar is. De eerste open stap krijgt het label *Nu* en een groene rand; de balk erboven toont hoeveel stappen klaar zijn, met *Opnieuw beginnen*. De voortgang staat per recept op het apparaat (`localStorage`) en vervalt 12 uur na het laatste vinkje, of als het recept in Mealie een ander aantal regels kreeg. Een recept uit Mealie heeft in het weekmenu en bij Recepten zoeken dezelfde voortgang.
- **Kookmodus.** *Start met koken* opent een schermvullend venster met één stap tegelijk in grote letters. *Volgende* zet de stap op klaar en gaat door (op de laatste stap: *Klaar*), *Vorige* bladert terug, *Stap klaar* zet een stap op klaar of weer open. Ook met de pijltjestoetsen en Page Up/Down, en op een telefoon of tablet door te vegen. *Ingrediënten* toont de ingrediënten met hun vinkjes: vanaf 900 px naast de stap, op een telefoon als paneel onderaan. Esc of het kruisje sluit, en de focus gaat terug naar de knop.
- **Scherm aan laten.** Een schakelaar bij het recept en in de kookmodus houdt het scherm aan zolang er een recept in beeld is (Screen Wake Lock). Standaard uit; de keuze geldt per apparaat. Terug naar de lijst, een ander scherm of het venster sluiten geeft het scherm weer vrij; komt het tabblad terug in beeld, dan vraagt de app het opnieuw. Alleen via HTTPS of `localhost` en in een browser die het kent; anders staat de schakelaar er niet.
- **Kopjes uit Mealie.** Een kopje boven ingrediënten (*Voor de saus*) en de titel van een stap staan nu in beeld. `GET /api/mealie/recipes/<slug>` geeft daarvoor bij een ingrediënt `section` en, als er titels zijn, `stepTitles` (even lang als `steps`); de bestaande velden blijven gelijk.
- **Breder venster van een maaltijd.** Op een groot scherm (vanaf 1000 px) is het venster van een maaltijd in het weekmenu 1040 px breed, zodat ingrediënten en stappen naast elkaar passen.
- **Handleiding (NL en EN)**: nieuw onderdeel *Koken met een recept* / *Cooking from a recipe*, een screenshot van de kookmodus, en nieuwe screenshots van het recept in het weekmenu, bij Recepten zoeken en bij Jumbo. De README's noemen de functie.
- **Ontwikkeling**: de kookweergave staat in `static/recipe_view.js` (geladen na `app.js`); `renderStoreRecipeDetail` en `renderMealRecipeBody` gebruiken allebei `renderCookView`. `mealRecipeLines` en `.picnic-recipe__list` zijn weg. Nieuwe tests in `test_recipe_view` (de hoeveelheden, beide bronnen, de weergave, de opslag, de kookmodus en de wake lock, in Node), `test_meal_plan_edit` (de kopjes uit Mealie) en `test_static_scripts`, dat nu ook faalt als een `const`, `let` of `class` in twee scripts staat.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#258](https://github.com/helmerzNL/MealieShopper/pull/258).

## v3.13.1

### Opgelost in 3.13.1

*English summary: the Recipes tab of Crisp under Stores stayed empty for most accounts. The Crisp app keeps the recipes you mark with a heart as Favourites on its recipes page, read from `GET /recipe/favorites`; only accounts in an experiment of Crisp see them under Saved by you (`/saved/list`), the only list 3.13.0 read. MealieShopper now reads both, so your favourite recipes show up, also in the bulk import, the recipe suggestions and the MCP tool. The route explorer reports the new route as well.*

- **Opgelost: je favoriete Crisp-recepten staan nu onder Winkels, Crisp, Recepten.** De tab bleef leeg. In de Crisp-app zet het hartje een recept bij je favorieten. Waar de app ze toont, hangt af van een experiment van Crisp (`202308:yourShop-saved`): wie dat heeft, ziet ze bij *Door jou bewaard* (`/saved/list`); alle anderen zien ze als *Favorieten* bovenaan de receptenpagina, uit `GET /recipe/favorites`. 3.13.0 las alleen `/saved/list`. Nu leest MealieShopper allebei, de favorieten eerst en zonder dubbelen. Lukt één van de twee niet, dan telt de andere; bij een verlopen koppeling stopt het meteen. Gevonden in de openbare Android-app (2.99, dezelfde als op 1 november): `app/domain/saved.ts`, `RecipeGroupsScreen.tsx` en `RecipeGroupScroller.tsx`.
- **Ook in de bulk-import, de receptvoorstellen en de MCP-tool**, want die gebruiken dezelfde lijst (`crisp.get_saved_recipes`).
- **Een onbekende soort recept valt niet meer stil weg.** Alleen de bekende soorten die geen gewoon recept zijn (bundels, eigen recepten, maaltijdboxen) blijven weg; een nieuwe soort van Crisp telt als recept.
- **Verkenner.** *Crisp-routes verkennen* heeft een nieuwe check, `recipeFavorites` (`GET /recipe/favorites`), met het aantal, `total`, de veldnamen en de soorten van de items; nog steeds zonder id's of namen.
- **Teksten.** De tab, de handleiding (NL en EN) en de MCP-tool spreken van favoriete recepten, zoals de app. `docs/onderzoek-crisp.md` beschrijft `/saved`, `/recipe/favorites` en het experiment.
- **Ontwikkeling**: `test_crisp` test beide bronnen, het samenvoegen, één bron die mislukt en een verlopen koppeling; de verkenner heeft nu 12 checks.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#257](https://github.com/helmerzNL/MealieShopper/pull/257).

## v3.13.0

### Nieuw in 3.13.0

*English summary: Crisp now has a Recipes tab under Stores, like Albert Heijn, Jumbo and Picnic. It shows the recipes you saved in the Crisp app (Saved by you), which MealieShopper already fetched for the favourites but left out. View a recipe, import it or plan it as before; with Select or Import all recipes they go into Mealie in one go, tagged Crisp. The saved Crisp recipes also count for the recipe suggestions under Offers, and a new read-only MCP tool, `list_crisp_recipes`, lists them. Searching all Crisp recipes stays under Find recipes.*

- **Winkels, Crisp, Recepten.** Een nieuwe eerste tab bij Crisp (`/stores/crisp/recipes`) met de recepten die je in de Crisp-app hebt bewaard (*Door jou bewaard*), als kaarten met foto, kooktijd en porties, en een filter op naam. `Bekijken`, `Importeer` en `Inplannen` werken zoals bij Picnic. MealieShopper haalde deze lijst al op voor de Favorieten, maar liet de recepten erin weg; er is geen nieuwe route bij Crisp nodig. Net als bij het zoeken laat het bundels, eigen recepten en maaltijdboxen weg.
- **In bulk in Mealie.** Bij de Crisp-recepten staan `Selecteren` en (voor de beheerder) `Alle recepten importeren`, zoals bij AH en Jumbo. Elk recept krijgt de tag *Crisp*; de bewaarde recepten zijn geen eigen indeling, dus er komt geen categorie bij. Bij alle recepten haalt de import de lijst eerst zelf bij Crisp op. Een verlopen koppeling (`crisp_auth`), een weigering (403 of 429, `crisp_blocked`) of een lijst die niet lukt (`crisp_saved_failed`) stopt de import met een eigen melding; een recept zonder ingrediënten en bereiding telt niet als fout van Crisp.
- **Receptvoorstellen.** `Recepten inlezen` onder **Aanbiedingen** leest ook je bewaarde Crisp-recepten in (tot 60), met *Recept uit Crisp* op de kaart. *Bekijken* opent de deellink van Crisp als het recept die heeft.
- **MCP-tool `list_crisp_recipes`** (alleen lezen): je bewaarde Crisp-recepten met id, naam, kooktijd, porties en labels, zonder afbeeldingen.
- **API.** `GET /api/crisp/recipes` zonder `q` geeft je bewaarde recepten (`{source: "saved", recipes, total}`), zoals `GET /api/picnic/recipes`; met `q` zoekt het zoals tot nu toe. `POST /api/mealie/bulk-import` en `/group` kennen `store: "crisp"`.
- **Verkenner.** *Crisp-routes verkennen* meldt bij de favorieten ook de veldnamen van een bewaard recept (`recipeFields`), de soorten recepten (`recipeKinds`) en of de lijst een cursor heeft. Nog steeds zonder id's, namen of prijzen.
- **Gewijzigd.** De bevestiging van *Alle recepten importeren* noemt de winkel in plaats van altijd Albert Heijn. Het receptdetail van Crisp geeft per ingrediënt ook de naam zonder hoeveelheid (`name`).
- **Handleiding** (NL en EN): de tab Recepten bij Crisp met een nieuwe screenshot (`crisp-recipes.png`, scenario in `scripts/screenshots/scenarios/35-crisp.js`), Crisp bij het importeren in bulk, de receptvoorstellen en de MCP-tool. De README's en `docs/onderzoek-crisp.md` zijn bijgewerkt.
- **Ontwikkeling**: nieuwe tests voor `crisp.get_saved_recipes`, de route, de verkenner, de bulk-import van Crisp (`CrispTest`), `_collect_crisp` en de MCP-tool. `test_ah_items_ui` en `test_store_switch` kennen de nieuwe tab: van Lijstjes naar Crisp opent nu Recepten.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#256](https://github.com/helmerzNL/MealieShopper/pull/256).

## v3.12.3

### Gewijzigd en opgelost in 3.12.3

*English summary: when you switch store under Stores, the section that was open now stays open, as it already did under Offers. From Lists at Albert Heijn to Jumbo opens Lists at Jumbo, from Orders at Jumbo to Picnic opens Orders at Picnic; if the other store does not have that section, its first tab opens. The store menu links to that same address, so opening it in a new tab gives the same screen. A store switch is now one step in the browser history instead of two, and switching store with a list open no longer jumps back to an earlier screen. Reload the page once after updating.*

- **Het open onderdeel blijft open bij een andere winkel.** Sta je onder Winkels op **Lijstjes** bij Albert Heijn en kies je Jumbo, dan opent **Lijstjes** van Jumbo, en van **Bestellingen** bij Jumbo naar Picnic opent **Bestellingen** van Picnic. Zo werkte Aanbiedingen al. Tot nu toe opende elke winkel bij **Recepten**. Heeft de andere winkel het onderdeel niet (Lijstjes bij Picnic, Recepten bij Crisp), dan opent zijn eerste tab. Recepten, Lijstjes, Bestellingen en Favorieten tellen als hetzelfde onderdeel bij elke winkel die ze heeft (`STORE_SECTIONS`, nu in `app.js`). Ook na het ontkoppelen van de open winkel blijft het onderdeel staan, bij Albert Heijn.
- **De winkelkeuze linkt naar wat een klik opent.** De winkels in het uitklapmenu linkten naar `/stores/<winkel>`; nu naar het onderdeel dat een klik opent (`/stores/jumbo/lists`), zodat openen in een nieuw tabblad (Ctrl, Cmd of de middelste knop) hetzelfde scherm geeft. Een adres zonder onderdeel, zoals een oude bladwijzer `/stores/jumbo`, opent nog steeds de eerste tab.
- **Opgelost: een winkelwissel was twee stappen in de geschiedenis.** Het adres kreeg eerst een tussenstap (de nieuwe winkel met het oude onderdeel, uit `resetOrders`) en daarna het echte adres, dus *Terug* moest twee keer. Nu opent de wissel één tab, die één keer laadt en één stap in het adres zet. `resetOrders` laadt zelf niet meer en zet het adres niet; dat doet de tab die erna opent. Een adres dat een andere winkel kiest (terug en vooruit, een bladwijzer) opent nu meteen het juiste onderdeel, zonder eerst de eerste tab van die winkel te laden.
- **Opgelost: van winkel wisselen met een lijstje open sprong terug in de geschiedenis.** Had je een lijstje uit het overzicht geopend, dan sloot de wissel het met *Terug* in de browser. Dat opende het vorige adres, van de vorige winkel of een scherm daarvoor, in plaats van de gekozen winkel. Nu sluit het lijstje zonder stap terug.
- **Handleiding** (NL en EN): wat er opent als je van winkel wisselt.
- **Ontwikkeling**: `tests/test_store_switch.py` draait `switchItemsStore` en `renderItemsStore` in Node met een nagebootste tabbalk: per wissel welk onderdeel opent, dat het één klik is, de links in het menu en het ontkoppelen.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#254](https://github.com/helmerzNL/MealieShopper/pull/254).

## v3.12.2

### Opgelost in 3.12.2

*English summary: the image cache could still fetch the same store photo twice. When a request saw no row yet for a new photo, and another request registered and fetched that photo before the first one registered it, the first request took the stored path for a deleted file, cleared it and fetched the photo again. It now checks whether the file is there first. This also fixes the image-cache test that still failed now and then. The fix was written for 3.6.3, which was never released; it now comes with 3.12.2.*

- **Een nieuwe winkelfoto wordt niet meer soms twee keer opgehaald.** De fix van 3.4.1 dekte één volgorde. Er was nog een tweede: verzoek B kijkt en ziet nog geen rij voor de foto, verzoek A registreert het adres, haalt de foto op en is klaar, en dan registreert B het adres. B zag daarna een pad en nam aan dat het bestand weg was (een opgeruimde map). B wiste dat pad en haalde de foto opnieuw bij de winkel op. Nu kijkt `image_cache.fetch` eerst of het bestand er is. Zo ja, dan is dat de foto van A en blijft het pad staan. Het ging alleen om een overbodige download, en het pad stond even leeg. De foto zelf was altijd goed.
- **Ontwikkeling**: `test_image_cache.FetchTest.test_two_requests_for_the_same_missing_image_share_one_download` faalde daardoor nog af en toe (zonder de fix 7 van de 300 keer los). Een nieuwe test speelt precies die volgorde na: zonder de fix twee downloads, met de fix één. Met de fix slaagde de test met twee threads 300 keer achter elkaar.
- De fix stond sinds 3.6.2 klaar op een eigen branch als versie 3.6.3, die nooit is uitgekomen. Hij is ongewijzigd overgezet naar 3.12.2; `fetch` was sindsdien niet veranderd. Bekend probleem uit [#234](https://github.com/helmerzNL/MealieShopper/pull/234) en [#245](https://github.com/helmerzNL/MealieShopper/pull/245).
- Pull request [#252](https://github.com/helmerzNL/MealieShopper/pull/252).

## v3.12.1

### Gewijzigd in 3.12.1

*English summary: the glass of the iOS-like bottom bar is now as see-through as in 3dprint_pricing. The Glass effect slider uses the same scale: opacity from 1 (0) to 0.15 (100), blur from 10 to 40 px, default 60 (0.49 opacity, 28 px blur). Up to about 30 the labels keep 4.5:1 over any photo; above that a very light or dark photo under the bar can get in the way, and the hint under the slider says so. With Reduce transparency on, the bar stays solid. Reload the page once after updating.*

- **Het glas net zo doorzichtig als in 3dprint_pricing.** De schuif *Glaseffect* (Instellingen, Weergave, Navigatiebalk, bij de iOS-achtige balk) heeft nu dezelfde schaal: dekking `1 - 0,0085 x` de stand, van 1 (0) tot 0,15 (100), en een vervaging van 10 tot 40 px. De standaard blijft 60 en is nu dekking 0,49 met 28 px vervaging (was 0,85), dus ook wie niets instelt ziet meer van de pagina door de balk. Wie al een stand koos, houdt die stand; het glas wordt bij dezelfde stand doorzichtiger.
- **Leesbaarheid.** Tot ongeveer stand 30 (dekking 0,75) halen de labels boven elke foto 4,5 : 1; daarboven kan een heel lichte of donkere foto onder de balk ze verstoren. De uitleg bij de schuif zegt dat nu. Met *Minder doorzichtigheid* aan op je toestel blijft de balk dicht, wat de schuif ook zegt.
- **Handleiding** (NL en EN) en de huisstijl: de nieuwe schaal.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#250](https://github.com/helmerzNL/MealieShopper/pull/250), vervolg op [#249](https://github.com/helmerzNL/MealieShopper/pull/249).

## v3.12.0

### Nieuw in 3.12.0

*English summary: the bar at the bottom of the installed app now looks like the phone's own apps, as the navigation-bar guideline of Flux76HQ/App-Guidance describes. An iPhone gets the floating glass bar, an Android phone the full-width Material bar with a pill behind the active icon. Under Settings, Appearance, Navigation bar you choose Automatic (the default, following the device), iOS-like or Android-like, and for the iOS-like bar how see-through the glass is (0 solid to 100 clearest). The choice is stored with your account; Automatic stays Automatic on every device. The active tab shows a filled icon and the others an outlined one. The clearest glass keeps every label at 4.5:1 over a black or white photo, which also fixes the active label of the dark theme that did not reach that before. Reload the page once after updating.*

- **De navigatiebalk van je telefoon.** De balk onderaan in de geïnstalleerde app (op een klein scherm) heeft twee uiterlijken, gekozen met `data-nav` op `<html>`: *iOS-achtig*, de zwevende glazen balk met afgeronde hoeken die er al was, en *Android-achtig*, de navigatiebalk van Material 3: over de volle breedte op de rand, een dicht vlak dat iets naar de accentkleur neigt, een pil van 64 bij 32 achter het actieve icoon en een vet label. Gebouwd volgens [`navigation-bar.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/navigation-bar.md) van Flux76HQ/App-Guidance, zoals in 3dprint_pricing.
- **Instellingen, Weergave, Navigatiebalk.** Kies *Automatisch* (de standaard: een Android-toestel krijgt de Android-balk, elk ander toestel de iOS-achtige, en eronder staat wat het nu is), *iOS-achtig* of *Android-achtig*. Bij de iOS-achtige balk stelt **Glaseffect** in hoe doorzichtig het glas is, van 0 (dicht) tot 100 (het doorzichtigst en het meest vervaagd); de balk verandert mee terwijl je schuift en de stand wordt bewaard als je loslaat. Weigert de server een keuze, dan komt de vorige terug met de melding.
- **Per gebruiker, en meteen goed.** De keuze staat bij je profiel (nieuwe kolommen `navbar_style` en `navbar_glass` in `user_profiles`, ook in de back-up). `PUT /api/profile/preferences` neemt `navbarStyle` (`auto`, `ios`, `android`) en `navbarGlass` (een geheel getal van 0 tot en met 100); iets anders geeft 400 en verandert niets. Bewaard wordt `auto`, niet wat dat op dit toestel is, zodat Automatisch op elk toestel opnieuw kiest. Het script in de kop van de pagina zet het toestel (`data-platform`, een iPad die zich als Mac meldt telt als iPad), de stijl en het glas vóór de eerste weergave, uit de kopie in je browser (`ms-appearance`), dus de balk verspringt niet bij het laden.
- **Gevulde en omlijnde iconen.** In de balk toont de actieve tab het gevulde icoon en de andere het omlijnde (Recepten, Aanbod, Winkels en Mandje; Boxen en Meer hebben geen omlijnde variant). Zo zie je de actieve tab ook zonder kleur.
- **Altijd leesbaar.** Het glas loopt van dekking 1 tot 0,75, met een vervaging van 10 tot 40 px; de standaard (60) is 0,85 en 28 px. Doorzichtiger kan niet: daaronder haalt geen labelkleur 4,5 : 1 boven zowel een witte als een zwarte foto. De labelkleuren van de balk zijn daarop aangepast (`--tabbar-ink-active` licht `#0F4C31`, donker `#C6EFDB`, `--tabbar-ink` donker `#C5D1CA` en donkergrijs `#CACBCD`). Dat lost ook op dat het actieve label in het donkere thema boven een lichte foto geen 4,5 : 1 haalde. De tinten van een eigen accentkleur worden op dezelfde doorzichtigste stand nagerekend. Met *Minder doorzichtigheid* aan op je toestel is de iOS-achtige balk dicht.
- **Geen inhoud onder de balk.** `--tabbar-h` (82 px iOS-achtig, 84 px Android-achtig) bepaalt de ruimte onder de pagina en de plek van de balk van *In bulk importeren*.
- **Handleiding** (NL en EN): de navigatiebalk onder Weergave en een nieuwe screenshot (`navbar.png`); de README's en de huisstijl (`docs/huisstijl.md`) beschrijven beide stijlen en waarom het glas niet doorzichtiger gaat.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#249](https://github.com/helmerzNL/MealieShopper/pull/249).

## v3.11.0

### Nieuw in 3.11.0

*English summary: Settings has a new part, Appearance, for every user. Pick the theme (follow device, light or dark), the background of the dark theme (the green one, or a new neutral dark grey) and an accent colour: ten choices or a custom colour from the colour picker. The choice is stored with your account, so it follows you to every device, and each user has their own. A colour that is too light or too dark for readable text is made a shade darker or lighter, measured against WCAG 4.5:1 in both themes and on the glass of the bottom bar; notices, discount badges and category colours keep their own colours. Regular users now see Settings in the avatar menu, with only Appearance in it. In the installed app (PWA) pinch and double-tap zoom are off and the page no longer bounces when you drag down at the top, so the title bar with the flags and the theme button stays put; in the browser zooming still works. Reload the page once after updating.*

- **Instellingen, Weergave, voor iedereen.** Een nieuw onderdeel onder **Instellingen** (`/settings/appearance`) met drie keuzes. **Thema:** *Apparaat volgen*, *Licht* of *Donker*. **Achtergrond in het donkere thema:** *Groen* (zoals tot nu toe) of het nieuwe *Donkergrijs*, een neutrale achtergrond met hetzelfde contrast, die rustig staat bij elke accentkleur. **Accentkleur:** tien rondjes (Basilicum, de standaard, Blauw, Indigo, Violet, Paars, Framboos, Oranje, Petrol, Bordeaux en Grafiet) of **Eigen kleur** met de kleurkiezer van je apparaat. De kleur geldt voor knoppen, links, de actieve tab (ook in de menubalk onderaan), de focusring, het selectievlak en de gloed bovenaan, en verandert meteen terwijl je kiest. Opgezet zoals de weergave van 3dprint_pricing, met de richtlijn *Web app design* van Flux76HQ/App-Guidance.
- **Per gebruiker bewaard.** Het thema, de donkere achtergrond en de accentkleur staan bij je profiel (nieuwe kolommen `theme`, `dark_background` en `accent` in `user_profiles`, ze zitten dus ook in de back-up). Op elk apparaat zie je na het inloggen je eigen kleuren, en iedere huisgenoot kiest zelf. `PUT /api/profile/preferences` neemt nu ook `theme` (`system`, `light`, `dark`), `darkBackground` (`green`, `grey`) en `accent` (`#rrggbb`, of leeg voor de huisstijl), alleen wat in het verzoek staat verandert, en een onbekende waarde geeft 400. `GET /api/auth/status` en `/api/profile` geven ze terug. Ook de maan- of zonknop bovenaan bewaart de keuze bij je account. Wie nog nooit een thema koos, houdt per browser wat daar stond. Een kopie staat in je browser (`ms-appearance`), zodat de pagina bij het laden niet eerst groen flitst.
- **Altijd leesbaar.** Een eigen kleur kan te licht (citroen) of te donker (zwart) zijn voor tekst. MealieShopper houdt de tint en schuift alleen de lichtheid tot de kleur 4,5 : 1 haalt op de vlakken van het lichte thema en van beide donkere achtergronden, en het label van de actieve tab in de menubalk ook op het glas boven een foto. Bij een eigen kleur staat onder de rondjes welke tinten je krijgt. Meldingen (gelukt, waarschuwing, fout), kortingsbadges en categoriekleuren volgen de accentkleur bewust niet, en het logo blijft groen. Een waarde die niet klopt (bijvoorbeeld uit een handmatig gewijzigde back-up) telt als geen keuze.
- **Instellingen in het avatar-menu voor iedereen.** Een gewone gebruiker ziet onder Instellingen alleen **Weergave**; Koppelen, Gebruikers, Auditlog en Systeem blijven van de beheerder (de server weigert ze zoals altijd), en elk ander adres onder `/settings` brengt een gewone gebruiker bij Weergave.
- **De geïnstalleerde app voelt meer als een app.** In de PWA (alleen daar, niet in de browser) staan knijp- en dubbeltikzoom uit (`user-scalable=no`, `touch-action: pan-x pan-y`, en op iOS het knijpgebaar), en veert de pagina niet meer mee als je bovenaan naar beneden sleept (`overscroll-behavior: none`): de titelbalk met de vlaggen en de themaknop blijft bovenaan plakken, en Android ververst de pagina niet onder je duim. In de browser blijft zoomen werken, voor wie de tekst groter wil.
- **Opgelost.** De actieve knop van een segmentbediening (de weergaven van de aanbiedingen) had witte tekst, ook in het donkere thema op het lichte groen: nu de tekstkleur voor op de accentkleur. De rand van de actieve tab en van een tag nam de kleur van "gelukt"; die heeft nu een eigen token (`--selection-border`) dat de accentkleur volgt.
- **Handleiding** (NL en EN): het nieuwe onderdeel [Weergave](docs/handleiding.md#weergave), het gedrag van de app op een telefoon, en een nieuwe screenshot (`appearance.png`). De huisstijl (`docs/huisstijl.md`) beschrijft hoe de accentkleur en het donkergrijs werken.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#248](https://github.com/helmerzNL/MealieShopper/pull/248).

## v3.10.0

### Nieuw in 3.10.0

*English summary: the main menu is shorter. The Import from URL tab is gone from the menu (the page still opens at `/import`). On a small screen the Profile tab is now called More, with a hamburger icon instead of your avatar, and the Basket moves from the tab bar into that menu, so the bottom bar has five items. The menu itself (also under your avatar on a large screen) shows your avatar and name, a line, Basket (small screen only), Profile, Settings and About, a line, and Log out, each with an icon. Reload the page once after updating.*

- **URL importeren staat niet meer in het menu.** De pagina *Recept importeren via URL* bestaat nog en opent op het adres `/import`; een recept van een winkel of een maaltijdbox zet je zoals altijd in Mealie vanuit *Recepten zoeken*.
- **Meer in plaats van Profiel op een klein scherm.** De laatste knop in de tabbalk (tot 640 px breed, in de app en in de browser) heet nu **Meer** en heeft een hamburger-icoon in plaats van je avatar. Hij opent hetzelfde menu als de avatar rechtsboven op een groot scherm, en licht op als het mandje, het profiel, de instellingen of *Over* open staat.
- **Het Mandje in het menu Meer.** Op een klein scherm staat **Mandje** niet meer in de tabbalk maar bovenaan het menu; de onderste menubalk heeft daardoor vijf items (Recepten, Aanbod, Winkels, Boxen, Meer). Op een groter scherm blijft Mandje een gewone tab. Het adres blijft `/cart`.
- **Het menu opnieuw ingedeeld, met iconen.** Bovenaan je avatar en naam, dan een lijn, **Mandje** (alleen op een klein scherm), **Profiel**, **Instellingen** (alleen de beheerder) en **Over**, dan een lijn en **Uitloggen**. Elk item heeft een icoon van Material Design Icons. De lijn boven Uitloggen verdwijnt met Uitloggen als inloggen uit staat. Het bolletje voor een update staat nu achter Instellingen in plaats van in de hoek van het menu, en bij het openen staat de focus op het eerste item in plaats van op Uitloggen.
- **Handleiding** (NL en EN), de README's en de huisstijl: het menu Meer en waar URL importeren gebleven is.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.

## v3.9.1

### Opgelost in 3.9.1

*English summary: after updating from 3.7.0 or older, Picnic could show no recipe photos at all for up to twelve hours. Those versions took a 403 from Picnic's storage for a block and paused all Picnic photos for twelve hours. The pause is stored in the database and kept running after the update, and because recipe photos have new addresses since 3.7.2, not one of them was in the cache yet. On its first start, 3.9.1 lifts a running Picnic pause once. A store that is paused now also shows under Settings, System, with the time the photos come back. Reload the page once after updating. Follow-up to [#236](https://github.com/helmerzNL/MealieShopper/issues/236).*

- **Na een update weer meteen foto's van Picnic** (vervolg op [#236](https://github.com/helmerzNL/MealieShopper/issues/236)). Een versie tot en met 3.7.0 las een 403 van de opslag van Picnic (een foto die er niet is) als een blokkade en zette alle foto's van Picnic twaalf uur stil. Die pauze staat in de database en liep na de update gewoon door, ook na een herstart. Omdat de receptfoto's sinds 3.7.2 een nieuw adres hebben, stond er nog geen enkele in de cache: de melder zag met 3.9.0 bij geen enkel recept een foto. Bij de eerste start van 3.9.1 heft MealieShopper een lopende pauze van Picnic één keer op (`image_cache.lift_stale_pauses`, in de log *Pauze van de afbeeldingencache opgeheven*). Een echte blokkade zet Picnic bij het volgende verzoek gewoon weer stil. Een pauze van een andere winkel blijft staan.
- **Een pauze is nu te zien.** Onder **Instellingen, Systeem** (bij *Opschonen*) staat per winkel die in pauze staat tot wanneer er geen nieuwe foto's komen. `GET /api/system/images` geeft daarvoor `paused` (`[{host, store, until}]`).
- **Handleiding** (NL en EN): waar je een pauze ziet.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.

## v3.9.0

### Nieuw en opgelost in 3.9.0

*English summary: an order or receipt now opens with four views, By category (a coloured section per category with the number of items, the saving and the subtotal), Cards in the colour of their category, a Table with sortable column headers, and Receipt for a receipt, plus a search field and category chips. Every purchase line gets a category (the AH taxonomy, otherwise the product name) and a colour, the discount badge of the Offers page measured against the regular price of the 30 days before the purchase, "3x before" and the bonus. The page header and every purchase in the list carry a status badge (Delivered, Planned, Imported, Receipt). In the purchases list a mouse on a product photo shows the photo large with its data, and a mouse on a purchase shows what it held per category. When AH pauses the linking of receipt lines and the fetching of photos (after a 403 or 429, or with the hourly budget spent), a notice above the list, on a receipt and under Settings, Link says so, with a button to fetch now. The saving in the list now uses the 30-day regular price. Reload the page once after updating.*

- **Een bestelling of bon met vier weergaven.** Onder de titel staat dezelfde balk als boven de aankopenlijst, met de weergaven **Per categorie** (standaard bij een bestelling), **Kaarten**, **Tabel** en **Bon** (alleen bij een kassabon, en daar de standaard). Per categorie heeft elke categorie een gekleurde kop met de naam, "13 stuks in 8 soorten", het bespaarde bedrag en rechts het subtotaal, met daaronder de regels met een kleine foto. De kaarten staan in de kleur van hun categorie, met de categorie in de metaregel. De tabel heeft de kolommen categorie, product, inhoud, aantal, prijs, korting, eerder en bedrag, en sorteert met een klik op een kolomkop. Met **Sorteren** kies je categorie, naam, bedrag of korting. Een andere sleutel begint in zijn eigen richting: categorie en naam oplopend, bedrag en korting aflopend. Op een bon is het subtotaal per categorie na de bonus, zodat de subtotalen optellen tot wat je betaalde. Een zoekveld (*Zoek in deze bestelling*) en de categoriechips ("Alle (47)" en per categorie het aantal) filteren samen, zonder verzoek aan de server. De weergave (apart voor een bestelling en een bon), de sortering en de opties blijven bewaard in je browser (`ms-orders-view`, `ms-orders-sort`, `ms-orders-options`). De twee knopjes van 3.7.0 rechts van de titel van een bon zijn opgegaan in die balk. Dit geldt voor bestellingen van AH en Jumbo, ingelezen bestellingen en kassabonnen.
- **Een categorie en een kleur per regel.** Elke regel van een aankoop hoort bij een van de negentien categorieën van de aanbiedingen. Bij een AH-bestelling komt die uit de taxonomie van AH, vertaald met een vaste tabel (`offer_categories`), en anders uit de naam van het gekoppelde product of de naam op de bon. De detailroutes (`GET /api/ah/orders/<id>`, `/api/ah/receipts/<id>`, `/api/jumbo/orders/<id>`, `/api/imported-orders/<winkel>/<ref>`) geven per regel `category`. Elke categorie heeft een lichte achtergrond en een accentkleur, met gedempte varianten in het donkere thema. De categoriechips zijn één component, gedeeld met de aanbiedingen, en tonen daar nu ook het kleurbolletje.
- **Badges per regel en per aankoop.** Per regel de kortingsbadge van de aanbiedingen (dezelfde kleurtreden en tooltip) tegenover de laagste gewone prijs van de 30 dagen vóór de aankoopdag, en zonder die prijs tegenover de prijs vóór bonus van AH als omlijnde badge. Verder "3x eerder" (eerdere aankopen van hetzelfde webwinkelproduct, alleen vóór deze aankoop) en de bonus. De detailroutes geven per regel `regularPrice`, `discount`, `discountSource` en `timesBefore`, en per aankoop `saved`, `savedPercent`, `statusKind` en `statusText`. In de kop van de aankoop staan de statusbadge (Afgeleverd, Ingepland, Geïmporteerd, Kassabon) en het bespaarde bedrag met het percentage. Op een kassabon hoort een bonusregel bij de bonregel waarvan de naam erop lijkt.
- **De aankopenlijst: een statusbadge en het bespaarde bedrag tegen 30 dagen.** Elke aankoop in de tabel, de kaarten en het overzicht heeft de statusbadge in plaats van de losse tekst van de soort, en de statusregel houdt alleen het bezorgmoment. Het bespaarde bedrag rekent nu tegen de gewone prijs van de 30 dagen vóór de aankoop, en zonder die prijs tegen de prijs vóór bonus van AH (zoals tot nu toe). De lijstroutes geven `statusKind` en `statusText` per aankoop.
- **De gewone prijs per regel, bewaard.** De gewone prijs van 30 dagen staat per regel in de nieuwe kolommen `regular_price` en `regular_checked_at` op `ah_order_items`, `jumbo_order_items` en `ah_receipt_items` (de tabellen zaten al in de back-up). Het openen van een aankoop vult de regels die hem nog missen, en de nachtelijke ronde van de aanbiedingen vult ze nu ook: hooguit vijfhonderd regels per winkel per nacht, en de aankopen van de laatste 60 dagen opnieuw (de prijsgeschiedenis kan nog aangroeien).
- **Hovervensters op de aankopenlijst.** Met een muis (niet op een telefoon of tablet) toont een productfoto in de kaarten of het overzicht na een tel de foto groot, met de naam, het merk en de inhoud, de categorie, "2x €1,19 = €2,38" met de kortingsbadge en hoe vaak je het eerder kocht. Een aankoop zelf (de rij, de kaart, de rij in de tabel) toont per categorie het aantal stuks en soorten, kleine foto's en het subtotaal, en onderaan het bespaarde bedrag en het totaal. Ook de grootste bestelling past op het scherm. Een klik op het venster opent de aankoop, Escape of een klik ernaast sluit het. Het venster leest alleen de detailroute van de aankoop en doet geen verzoek aan AH.
- **De AH-pauze van het koppelen in beeld.** Als AH een 403 of 429 geeft, liggen het koppelen van bonregels en het ophalen van foto's twaalf uur stil, en het uurbudget is 120 zoekopdrachten. Dat zag je nergens. Nu staat er een melding boven de aankopenlijst bij een pauze, een opgebruikt uurbudget of werk dat op de nacht wacht ("Nog 19 artikelen op 6 bonnen zonder product ... Volgende ronde vannacht om 03:00."), op een kassabon met regels zonder product (de knop telt de regels die een ronde opzoekt, en zocht AH ze de laatste 30 dagen al op, dan staat de knop uit en komt de volgende poging vanzelf), en voor de beheerder altijd onder **Instellingen, Koppelen** in het vak *Producten koppelen en foto's ophalen bij AH* (toestand, een meter voor het uurbudget, te doen, de laatste en de volgende ronde). Met **Nu ophalen** start elke ingelogde gebruiker een ronde zoals 's nachts, of op een bon alleen voor die bon. **Nu toch proberen** wist bij een pauze de pauze en mag alleen de beheerder (een nieuwe 403 of 429 zet de pauze opnieuw op twaalf uur). **Foto's aanvullen** haalt alleen foto's, zonder zoekopdrachten. Nieuwe routes: `GET /api/ah/linking` (de toestand, voor iedereen) en `POST /api/ah/linking/run` (met `receiptId`, `force` of `images`, een ronde tegelijk, een tweede geeft 409). Elke ronde op afroep staat in het auditlog.
- **Opgelost.** Een bonregel van één stuk zonder prijs (AH drukt dan alleen het bedrag af) rekende als 100% bespaard: hij gebruikt nu het bedrag gedeeld door het aantal. Een bonusregel die bij geen regel past telt mee in het bespaarde bedrag van de bon. Een bonusregel die groter is dan zijn regel (een actie over meer artikelen) geeft die regel hooguit zijn eigen bedrag en de rest telt in het totaal, zodat de lijst, de bon en het hovervenster hetzelfde bespaarde bedrag tonen. De lijst rekent een bonregel zonder prijs met het bedrag gedeeld door het aantal.
- **Handleiding** in beide talen en drie nieuwe screenshots (`order-categories.png`, `order-hover.png`, `linking-notice.png`).
- **De knop Koppelen van 3.8.0 in de nieuwe weergaven.** De knop met de schakel staat op het bonnetje achter de omschrijving, rechts op elke kaart en achter de naam in Per categorie en in de tabel, bij een kassabon en een ingelezen bestelling. Na koppelen tekent de open aankoop opnieuw in de gekozen weergave. De gewone prijs van 30 dagen, de telling "3x eerder" en de melding over regels zonder product volgen je eigen koppeling, net als de bon zelf.
- Pull request [#241](https://github.com/helmerzNL/MealieShopper/pull/241), bovenop [#235](https://github.com/helmerzNL/MealieShopper/pull/235) en [#240](https://github.com/helmerzNL/MealieShopper/pull/240).

## v3.8.1

### Opgelost in 3.8.1

*English summary: a Picnic recipe sent to Mealie arrived with only its title and photo, without ingredients and method. Mealie reads the recipe we send with the recipe-scrapers library, which picks its scraper by the host of the source link. For picnic.app it has its own scraper that only reads the Picnic website and ignores our schema.org data. MealieShopper now hands the recipe to Mealie under a neutral link, so the general schema.org scraper reads all of it, and then sets the link to Picnic as the source again. A Picnic recipe that reached Mealie empty before 3.8.1 stays empty: delete it in Mealie and send it again. Fixes [#242](https://github.com/helmerzNL/MealieShopper/issues/242).*

- **Picnic-recepten komen weer compleet in Mealie** ([#242](https://github.com/helmerzNL/MealieShopper/issues/242)). Een recept van Picnic kwam in Mealie binnen met alleen de titel en de foto. MealieShopper stuurt het recept als schema.org Recipe (JSON-LD) naar Mealie (`POST /api/recipes/create/html-or-json`), en Mealie leest dat met de bibliotheek *recipe-scrapers*. Die kiest zijn scraper op de host van de meegegeven link (`https://picnic.app/#recipe-…`), en heeft voor `picnic.app` een eigen scraper die alleen de website van Picnic leest en de JSON-LD negeert. Ingrediënten en bereiding bleven daardoor leeg. Nu gaat zo'n recept onder een neutrale link naar Mealie (`https://mealieshopper.invalid/recipe`), zodat de algemene schema.org-scraper alles leest. Daarna zet MealieShopper de link naar Picnic terug als bron (`PATCH` van `orgURL`), zodat *Inplannen* het recept later weer vindt. De andere winkels en maaltijdboxen houden hun eigen link: daar leest recipe-scrapers de JSON-LD wel (nagekeken voor AH, Jumbo, Crisp, HelloFresh en Marley Spoon).
- **Een Picnic-recept dat al leeg in Mealie staat**, blijft leeg: *Inplannen* gebruikt het recept dat er al staat. Verwijder het in Mealie en zet het opnieuw in Mealie.
- **Handleiding** (NL en EN): wat er van een Picnic-recept in Mealie komt, en wat je doet met een recept dat leeg binnenkwam.

## v3.8.0

### Nieuw in 3.8.0

*English summary: a line on a receipt or in an imported order that MealieShopper does not recognise (a shortened name such as "COTTAGE CH", or an email name that differs from the webshop) can now be linked to a product of the store yourself. A chain-link button on the line opens Link a product: a search field filled with the name of the line and the products of that store to choose from. From then on every line with the same AH article number or the same name is recognised as that product, on every other and future receipt, in the imported orders of Albert Heijn, Jumbo, Picnic and Crisp, and in the Price history. Your own link always comes before the automatic one, so Choose another product also fixes a wrong match, and Unlink brings the automatic one back. The links are part of the backup. Implements [#238](https://github.com/helmerzNL/MealieShopper/issues/238) in [#240](https://github.com/helmerzNL/MealieShopper/pull/240).*

- **Niet herkende producten zelf koppelen** ([#238](https://github.com/helmerzNL/MealieShopper/issues/238), [#240](https://github.com/helmerzNL/MealieShopper/pull/240)). Een regel op een kassabon of in een ingelezen bestelling die MealieShopper niet aan een product van de winkel koppelde (een afgekorte bonnaam als *COTTAGE CH*, of een naam uit een bevestigingsmail die anders is dan in de webwinkel), krijgt een knop met een schakel: op het bonnetje achter de omschrijving, en in de productweergave van een bon en bij een ingelezen bestelling rechts op de kaart. **Product koppelen** zoekt bij de winkel met de naam van de regel; kies het juiste product. De regel toont meteen dat product (naam, foto, link naar de prijsgeschiedenis) en de aankopen tellen in de Prijsgeschiedenis bij dat product in plaats van als los product (`bon:…` of `import:…`).
- **Eén keer koppelen is genoeg.** Daarna herkent MealieShopper elke regel met hetzelfde AH-artikelnummer, of zonder artikelnummer met dezelfde naam, op elke andere en volgende bon en in de ingelezen bestellingen van Albert Heijn, Jumbo, Picnic en Crisp. Daarmee komen ook de foto's in de bonnenlijst, **Alleen eerder gekocht** en **Alles in mandje** bij een ingelezen Picnic- of Crisp-bestelling goed. Een artikelnummer dat je zelf koppelde, zoekt de app niet meer op bij AH; dat spaart zoekopdrachten.
- **Je eigen keuze gaat voor.** Bij een regel die al bij een product hoort, ook automatisch, heet de knop **Ander product kiezen**: zo zet je een verkeerde koppeling recht. **Ontkoppelen** haalt je eigen koppeling weg; daarna geldt weer de automatische.
- **Opslag en back-up.** Een nieuwe tabel `product_links` (winkel, soort `article` of `name`, sleutel, product en wat je koos), apart van `ah_receipt_products`: die is opnieuw op te zoeken en zit niet in de back-up, je eigen koppelingen wel (ook in de telling bij het terugzetten). De foto's van gekoppelde producten gaan mee in de afbeeldingencache.
- **API.** `GET /api/product-links`, `PUT /api/product-links`, `DELETE /api/product-links/<winkel>/<soort>/<sleutel>` en `GET /api/product-links/search?store=&q=` (één zoekopdracht bij de winkel, in dezelfde vorm voor alle winkels; de gevonden prijzen gaan ook in de Prijsgeschiedenis). Een bonregel heeft `link` als je hem zelf koppelde; een regel van een ingelezen bestelling heeft ook `productTitle` en `link`.
- **Handleiding** (NL en EN): *Zelf koppelen* bij de kassabonnen, met een nieuw screenshot (`receipt-link.png`), en de README's.

## v3.7.2

### Opgelost in 3.7.2

*English summary: the photos of newer Picnic recipes show again. Picnic names a recipe photo by the box it fits in (`300x300`, `1000x1000`); only older recipes also have the names `medium` and `large`. MealieShopper asked for `medium.png` and `large.png`, which a newer recipe answers with 403 AccessDenied, so its photo stayed away in the recipe list, in the recipe and in a recipe imported into Mealie. Version 3.7.1 kept that one 403 from pausing all Picnic photos, but did not fetch the photo itself. A recipe photo now uses the size in pixels, which every recipe has (an older recipe gets the same photo as before). Product photos keep their names. Fixes [#236](https://github.com/helmerzNL/MealieShopper/issues/236).*

- **Picnic: de foto's van nieuwere recepten staan er weer** ([#236](https://github.com/helmerzNL/MealieShopper/issues/236)). Na 3.7.1 hielden de recepten zonder foto de andere foto's niet meer tegen, maar die recepten bleven zonder foto. Ze hebben er wel een: Picnic noemt een receptfoto naar het vak waar hij in past (`300x300`, `1000x1000`), en alleen oudere recepten hebben ook de namen `medium` en `large`. MealieShopper vroeg om `medium.png` (de lijst) en `large.png` (het recept), en daarop antwoordt een nieuwer recept met `403 AccessDenied`. Nu vraagt hij om `300x300.png` en `1000x1000.png`: die heeft elk recept, en bij een ouder recept is het dezelfde foto als `medium` en `large`. Dat geldt voor *Recepten van Picnic*, een geopend recept, een recept dat je naar Mealie haalt en de recepten bij de aanbiedingen. Productfoto's van Picnic houden hun namen.
- **De index van recepten bij de aanbiedingen** heeft de oude adressen van Picnic-recepten nog; die zet de app bij de start een keer om, zonder dat je de index opnieuw hoeft in te lezen.
- **Een recept dat je al naar Mealie haalde**, houdt daar zijn ontbrekende foto. Verwijder je het in Mealie en haal je het opnieuw op, dan komt de foto mee.

## v3.7.1

### Opgelost in 3.7.1

*English summary: one Picnic recipe without a photo no longer hides the photos of all other recipes (and Picnic products) for twelve hours. Picnic keeps its photos in Amazon S3, which answers a photo that does not exist with 403 AccessDenied instead of 404. The image cache took that 403 for a block and paused the whole Picnic host for twelve hours. It now counts such a 403 as a missing photo, like a 404, and only pauses a host for a real block (any other 403, or a 429). Fixes [#236](https://github.com/helmerzNL/MealieShopper/issues/236).*

- **Picnic: één recept zonder foto houdt de andere foto's niet meer tegen** ([#236](https://github.com/helmerzNL/MealieShopper/issues/236)). Onder *Recepten van Picnic* bleven foto's weg. Picnic bewaart zijn foto's in Amazon S3, en S3 antwoordt op een foto die er niet is met `403 AccessDenied` (in XML), niet met een 404. De afbeeldingencache las elke 403 als een blokkade en zette de hele host `storefront-prod.nl.picnicinternational.com` twaalf uur stil. Na drie recepten zonder foto kwam er daardoor twaalf uur lang geen enkele foto van Picnic meer, ook niet van producten. Nu telt een 403 met de foutmelding van S3 (`AccessDenied` of `NoSuchKey`) als een foto die ontbreekt, net als een 404: dat ene adres wacht een week, de rest komt gewoon. Een echte blokkade (een andere 403, of een 429) zet de host nog steeds twaalf uur stil.
- **Een pauze van voor deze versie** loopt nog af: hooguit twaalf uur na de eerste fout. Daarna komen de foto's vanzelf.
- **Handleiding** (NL en EN): de foto's van recepten gaan ook via de cache, en een foto die de winkel niet heeft, houdt de andere niet tegen.

## v3.7.0

### Nieuw en opgelost in 3.7.0

*English summary: a receipt opens as a narrow paper strip again (it had grown as wide as the page) and gets a second view, Products: the same lines as product cards with the webshop name and photo, the bonus lines below, and the item count, total, payment method and bonus amount under the title; the choice is remembered. Receipt photos no longer wait for the nightly round alone: opening a receipt fetches the photos of its own lines, opening the receipts list starts a small background round, and the nightly round does five hundred instead of two hundred. In the purchases overview the photo strip moved to the right at a fixed width, so every row starts its text at the same place. The image cache applies one strict address check to tokens, table addresses and redirects, and the small review points of the Offers page (#231) are fixed. Reload the page once after updating.*

- **Kassabon als bonnetje én als producten.** Een bon opent weer als een smalle strook papier in het midden (hij was zo breed als de pagina geworden en zag er niet meer uit als een bon). Met de twee knopjes rechts van de titel wissel je naar **Producten**: dezelfde regels als productkaarten met de naam en de foto van het webwinkelproduct waaraan de regel gekoppeld is (een regel die nog niet gekoppeld is houdt de afgekorte naam van de bon), de bonusregels eronder, en onder de titel het aantal artikelen, het totaal, de betaalwijze en het bonusbedrag. De keuze blijft bewaard in je browser. Bij zoeken lichten de gevonden regels in beide weergaven op. Op een breed scherm scrolt de bon in zijn eigen vak, zodat de pagina niet schuift.
- **Bonfoto's komen sneller.** De foto's bij gekoppelde bonregels kwamen alleen uit de nachtelijke ronde (200 per nacht): met tweeduizend gekoppelde regels bleven de bonnen maanden zonder foto. Het openen van een bon haalt nu de foto's van zijn eigen regels op (`POST /api/ah/receipts/<id>/products` geeft `images`), het openen van de lijst met aankopen start een kleine ronde op de achtergrond (hooguit vier verzoeken aan AH, honderd producten), en de nachtelijke ronde doet er vijfhonderd.
- **Overzicht: de foto's rechts.** In het overzicht van de aankopen staat de strook foto's rechts, op een vaste breedte, en de details links: een bon zonder foto's (een icoon) en een bestelling met zes foto's beginnen hun tekst nu op dezelfde plek. Op een telefoon staan de foto's onder de details.
- **Afbeeldingencache: één strikte adrescontrole.** `GET /api/images/<token>` liet elk adres door dat op een winkelhost leek, ook `http` en een adres met een gebruikersnaam of stuurteken voor de host. Een token uit de browser, een adres uit een tabel en een doorverwijzing van een CDN krijgen nu dezelfde controle (`https`, en de parser die het verzoek verstuurt leest dezelfde host).
- **Aanbiedingen: de kleine punten uit de review van [#231](https://github.com/helmerzNL/MealieShopper/pull/231).** De telling *Daarvan n echt voordelig* telt dezelfde aanbiedingen als het totaal ervoor. De compacte lijst op een telefoon: de badge onder het plaatje, de naam op twee regels, de prijs met de eenheid rechts en het prijslabel eronder. De omlijnde oranje badge en de grijze 0%-badge zijn in het donkere thema leesbaar. Een kaart zonder foto krijgt een icoon, de Bonus Box-chip staat onder de actie, de tooltip *was al eens lager* noemt altijd het jaar, en een fout in de prijsladder laat het oordeel en de badge staan. De sortering en de badge draaien nu ook in een test in Node.
- **Handleiding** in beide talen en een nieuw screenshot (`receipt-products.png`).

## v3.6.2

### Aangepast en verwijderd in 3.6.2

*English summary: an imported order now always shows as Delivered, also when it was imported before 3.5.0 or the email named no status, so old imports get a status without importing them again. The Import old purchases dialog no longer has the button "Remove imported purchases", which removed every imported order of a store at once. Each imported order has its own bin under Orders, so you remove them one at a time there.*

- **Een ingelezen bestelling staat altijd als *Afgeleverd*.** Bij een bestelling die je vóór 3.5.0 inlas, of waarvan de mail geen status noemde, bleef de status leeg, terwijl een afgelopen bestelling uit je account altijd *Afgeleverd* toont. Een ingelezen bestelling telt als gekocht en een geannuleerde wordt niet ingelezen, dus `GET /api/imported-orders/<winkel>` geeft nu altijd `status: "delivered"`. Bij AH en Jumbo staat er *Afgeleverd*, bij Picnic en Crisp de status van die winkel. Je hoeft oude imports dus niet opnieuw in te lezen. Alleen het tijdvak komt nog steeds uit de mail, of anders van de bestelling uit je account zonder producten die erdoor wegvalt.

- **De knop *Ingelezen aankopen verwijderen* is weg uit de dialoog *Oude aankopen importeren*.** Die haalde alle ingelezen bestellingen van een winkel in één keer weg. Elke ingelezen bestelling heeft bij **Bestellingen** haar eigen prullenbak, dus daar verwijder je ze een voor een. De teksten van de knop (`offers.import.remove`, `offers.import.remove_confirm`, `offers.import.removed`) zijn ook weg. Een nieuwe test (`ImportDialogTest`) faalt als de knop terugkomt.
- **Handleiding** (NL en EN): weghalen gaat per bestelling, met haar eigen prullenbak.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.

## v3.6.1

### Aangepast in 3.6.1

*English summary: for an imported order the archive icon now comes after the word "Order" instead of before it, so "Order" lines up in the Kind column of the table, on the cards and in the overview.*

- **Het archief-icoon staat achter *Bestelling*.** Bij een ingelezen bestelling stond het icoon in 3.5.0 vóór het woord, waardoor *Bestelling* in de kolom Soort niet onder die van de andere bestellingen uitlijnde. Nu staat het erachter, in de tabel, op de kaarten en in het overzicht (`purchaseKindLabel`). De tooltip en de schermlezertekst (*Geïmporteerd uit je bevestigingsmails*) zijn hetzelfde gebleven. Bij Picnic en Crisp staat het icoon nog steeds voor de datum op de kaart.
- **Handleiding** (NL en EN): *archiefdoos erachter*.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Vervolg op issue [#229](https://github.com/helmerzNL/MealieShopper/issues/229) en pull request [#230](https://github.com/helmerzNL/MealieShopper/pull/230).

## v3.6.0

### Nieuw in 3.6.0

*English summary: the Offers page is built to scan. Every offer is a card with the photo on top, a coloured discount badge (the percentage below the lowest regular price of the last 30 days, outlined when the app only knows the store's claim) and a price label from the history: Lowest price ever, Lowest in 12 months, Lowest in 6 months, Lowest since <month>, or "€6.99 in May" when it has been lower before. Hovering the photo (tapping on a phone) shows the large photo with a one-year price sparkline. Above the cards: a search field, an "Only real deals" switch, sorting by store order, biggest discount, lowest price per unit or lowest ever first, and a switch to a compact one-line-per-offer list. `GET /api/offers` gives `badge` and `priceLabel` per offer (`bestInYear` is gone), and `GET /api/prices/history` takes `product=<id>` for one product. Reload the page once after updating.*

- **De aanbiedingen scannen.** Elke aanbieding is nu een kaart met het plaatje bovenaan, groot genoeg om het product te herkennen, en de links *Vergelijk verpakkingen* en *Prijsgeschiedenis* netjes op één rij onderaan.
- **Kortingsbadge.** Linksboven op het plaatje staat de korting als getal: het verschil met de laagste gewone prijs van de laatste 30 dagen, in de kleuren van het userscript *Albert Heijn kortingspercentages* (donkergroen vanaf 50%, lichtgroen 35%, geel 20%, oranje 10%, rood daaronder). Kent de app nog geen gewone prijs van het product, dan staat de claim van de winkel erop, omlijnd. De uitleg (het oordeel, de prijs waarmee vergeleken is, een opgepompte prijs ervoor) staat in de tooltip.
- **Prijslabel uit de geschiedenis.** Rechtsonder op het plaatje: *Laagste prijs ooit*, *Laagste in 12 mnd*, *Laagste in 6 mnd* (groen), *Laagste sinds mei* of *€6,99 in mei* (grijs: was al eens lager). Uit de eerdere aanbiedingen, de bewaarde prijzen en je aankopen, ook de ingelezen. Een venster telt pas als de app het product zo lang volgt. *Laagste actieprijs in een jaar* vervalt (`bestInYear` in `GET /api/offers`). *n× dit jaar* blijft als chip.
- **Hover-zoom.** Ga met de muis over het plaatje: het grote plaatje met daaronder het prijsverloop van het afgelopen jaar (gewone prijs, aanbiedingen, aankopen, de prijs van nu). Op een telefoon opent een tik hetzelfde als venster. `GET /api/prices/history?store=<winkel>&product=<id>` geeft één product.
- **Zoeken, filteren en sorteren.** Een zoekveld over alle categorieën, de schakelaar *Alleen echt voordelig* (met het aantal achter de status), en sorteren op winkelvolgorde, hoogste korting, laagste prijs per eenheid of laagste ooit eerst. De keuzes blijven bewaard in je browser.
- **Compacte lijst.** Een schakelaar naast de sortering wisselt naar één regel per aanbieding met een kopregel: plaatje, korting, product, prijs, per eenheid, geschiedenis en twee iconen.
- **Handleiding en README**: de badge, de labels, de werkbalk en de twee weergaven.
- Pull request [#231](https://github.com/helmerzNL/MealieShopper/pull/231).

## v3.5.0

### Nieuw en opgelost in 3.5.0

*English summary: imported orders (from your confirmation emails) now look like orders from your account, at every store (issue #229). An old Jumbo order that is in your account without products is no longer listed twice next to its imported version: the empty one drops out of the list. The import also reads the status and the delivery time from the email and shows them like the store does ("Delivered · 18:00-20:00"); a cancelled order is not imported. Every line of an imported order is matched by name to a product of the store, as in the Price history, so the order gets the same photos, brand and size, and at Picnic and Crisp the same card and "Add all to basket". The kind is now simply "Order", with the archive icon in front.*

- **Geen dubbele bestelling meer.** Sinds 3.3.1 (#222) kun je een oude Jumbo-bestelling inlezen die zonder producten in je account staat. Daarna stond ze twee keer bij Bestellingen: *producten nog niet opgehaald* en de ingelezen versie. Nu valt een bestelling uit je account zonder producten weg uit de lijst als er een ingelezen bestelling is met hetzelfde nummer of op dezelfde dag (dezelfde regel als `price_import.account_orders`, omgekeerd). Ze blijft wel bewaard: haalt MealieShopper de producten later toch op, dan staat ze er weer en valt de ingelezen versie weg. Dat geldt voor alle winkels, met de datum van elke winkel (`importedOrdersCover` in `orders.js`).
- **Status en bezorgmoment uit de mail.** De prompt vraagt nu ook om `status` (`delivered`, `cancelled` of null) en `deliveryWindow` (`start` en `end` als UU:MM). Een ingelezen bestelling toont ze zoals de winkel dat bij een eigen bestelling doet: *Afgeleverd · 18:00-20:00* bij AH en Jumbo, de status van Picnic of Crisp met het tijdvak. **Controleren** laat ze al zien. Een geannuleerde bestelling wordt niet ingelezen; een status of tijd die niet te lezen is, blijft leeg, met een opmerking. Noemde de mail geen status of tijd, dan neemt de lijst die over van de bestelling uit je account die erdoor wegviel. Ze staan in drie nieuwe kolommen van `imported_purchases` (`status`, `slot_start`, `slot_end`); een database of back-up van daarvoor werkt gewoon, de oude rijen houden ze leeg.
- **Dezelfde weergave als een bestelling uit je account.** Elke regel uit de mail wordt bij het lezen op naam gekoppeld aan een product van de winkel, met dezelfde koppeling als de Prijsgeschiedenis (`prices.link_names`). Zo krijgt een ingelezen bestelling in de tabel, de kaarten en het overzicht de foto's, het voordeel en de bonus, en in het detail per product de foto, het merk, de inhoud en de link naar zijn prijsgeschiedenis. Zonder foto van de winkel komt de bewaarde afbeelding. Bij Picnic en Crisp heeft een ingelezen bestelling dezelfde kaart en hetzelfde detail als een bezorgde bestelling, en zet **Alles in mandje** de gekoppelde producten in je mandje; de melding noemt hoeveel regels bij geen product horen.
- **Soort *Bestelling*, met het archief-icoon ervoor.** Het woord *Geïmporteerd* is weg uit de tabel, de kaarten en het overzicht. Het archief-icoon zegt als tooltip en voor een schermlezer *Geïmporteerd uit je bevestigingsmails*; de grijze kleur van de soort en de prullenbak blijven.
- **API.** `GET /api/imported-orders/<winkel>` geeft per bestelling ook `status`, `slotStart`, `slotEnd`, `images`, `savings` en `bonusCount`; `GET /api/imported-orders/<winkel>/<ref>` per regel ook `productId`, `image`, `brand` en `unitSize`. Het rapport van `POST /api/prices/import` geeft per bestelling `status`, `slotStart`, `slotEnd` en `notes`.
- **Handleiding** (NL en EN): ingelezen bestellingen bij Bestellingen, de weergaven en *Oude aankopen importeren*.
- **Ontwikkeling**: nieuwe tests in `tests/test_price_import.py` (status en bezorgmoment, annulering, de migratie, de koppeling voor AH, Jumbo, Picnic en Crisp) en `tests/test_imported_orders_ui.py` (in Node: het verbergen, de status, het label, de vorm voor Picnic en Crisp).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Issue: [#229](https://github.com/helmerzNL/MealieShopper/issues/229), vervolg op [#222](https://github.com/helmerzNL/MealieShopper/issues/222).

## v3.4.1

### Opgelost in 3.4.1

*English summary: in rare cases the image cache fetched the same store photo twice. A request that found no photo, but took over the download just after another request had finished it, fetched it again. The request that takes over the download now checks the cache once more first. This also fixes the image-cache test that failed now and then. The Home Assistant add-on files of 3.4.0 contained leftover merge-conflict markers and two version lines, so Home Assistant could not read them; they are clean again.*

- **Een winkelfoto wordt niet meer soms twee keer opgehaald.** Vroegen twee verzoeken tegelijk dezelfde foto op, dan wachtte het tweede op de download van het eerste. Keek het tweede verzoek net in de cache voordat de foto er was, maar pakte het het slot pas nadat de eerste download klaar was, dan zag het geen lopende download meer en haalde het de foto opnieuw bij de winkel op. Nu kijkt het verzoek dat de download op zich neemt eerst nog één keer in de cache (`image_cache.fetch`). Het ging alleen om een overbodige download; de foto zelf was altijd goed.
- **Ontwikkeling**: `test_image_cache.FetchTest.test_two_requests_for_the_same_missing_image_share_one_download` faalde daardoor af en toe. Een nieuwe test speelt precies die volgorde na: zonder de fix twee downloads, met de fix één.
- **De Home Assistant-add-ons zijn weer leesbaar.** In 3.4.0 stonden in beide `config.yaml`'s (`homeassistant/mealieshopper` en `homeassistant/mealieshopper-beta`) nog conflictmarkeringen van een merge, met twee `version:`-regels. Dat is geen geldige YAML, dus de Supervisor van Home Assistant kon de add-on niet lezen. Ze zijn weer schoon, en een nieuwe test (`test_the_addon_files_have_no_merge_conflict_left`) faalt als het weer gebeurt.
- Pull request: [#228](https://github.com/helmerzNL/MealieShopper/pull/228).

## v3.4.0

### Nieuw in 3.4.0

*English summary: under Stores > Orders the same filtered list now comes in three views, as in Sonarr: the table, cards with a collage of product photos, and an overview with a photo strip and the details per purchase. A switch above the list, a Sort menu (date, amount, products, ascending or descending, also by clicking a table header) and an Options menu (card size, what a card shows) remember your choice in the browser. Each kind of purchase has a small colour mark: green for an order, yellow for a receipt, grey for an imported order, next to the text. The list routes carry a summary per purchase (photos, product names, amount saved, payment method) so no request per purchase is needed. The MCP tools are unchanged.*

- **Drie weergaven van je aankopen.** Onder **Winkels > Bestellingen** staat dezelfde gefilterde lijst als **Tabel**, als **Kaarten** met een collage van productfoto's (vier, of zes bij grote kaarten) en als **Overzicht** met een strook foto's en de details per aankoop. Een schakelaar rechts boven de lijst wisselt, en de keuze blijft bewaard in je browser. Kassabonnen tonen de foto's van de gekoppelde webwinkelproducten. Een ingelezen bestelling of een bon zonder koppelingen krijgt het pictogram van zijn soort en de eerste productnamen.
- **Sorteren en opties.** Het menu **Sorteren** kiest datum, bedrag of aantal producten, oplopend of aflopend (een klik op een kolomkop in de tabel doet hetzelfde). Een aankoop waarvan de producten nog niet zijn opgehaald staat onderaan. Het menu **Opties** kiest de kaartgrootte (klein, middel, groot) en wat een kaart laat zien: status en bezorgmoment (of de betaalwijze van een bon), het bespaarde bonusbedrag en de productnamen. Bij zoeken tonen de kaarten en het overzicht de gevonden regels.
- **Een kleur per soort.** Een bolletje in de tabel en bij de vinkjes, een streep op de kaarten en in het overzicht: groen voor een bestelling, geel voor een kassabon, grijs voor een ingelezen bestelling. De tekst van de soort blijft overal staan.
- **Snel.** Wisselen van weergave of sorteren tekent opnieuw uit de lijst die er al staat, zonder verzoek. De foto's laden lui en alleen wat in beeld is wordt opgebouwd. Elke weergave vult het scherm en schuift in zijn eigen vak.
- **API.** `GET /api/ah/orders`, `GET /api/jumbo/orders`, `GET /api/ah/receipts` en `GET /api/imported-orders/<winkel>` geven per aankoop een samenvatting mee: `images` (hooguit zes), `productNames` (hooguit vier), `savings` en `bonusCount` (bestellingen), `payment` (bonnen). De MCP-tools geven dezelfde lijsten zonder die velden.
- **Handleiding**: de drie weergaven, met nieuwe screenshots.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#227](https://github.com/helmerzNL/MealieShopper/pull/227), bouwt op [#216](https://github.com/helmerzNL/MealieShopper/pull/216) en [#217](https://github.com/helmerzNL/MealieShopper/pull/217).

## v3.3.3

### Opgelost in 3.3.3

*English summary: for eggs the check list showed nothing under the suggestion, and Prefer organic said no organic option was found although "AH Biologisch Eieren S M L" was in the results. The kind of product came from the suggestion's whole name ("witte vrije uitloopeieren m"), so no other eggs counted. The kind now follows the ingredient when the suggestion names it, egg sizes S, M and L are a size, and at Albert Heijn the product must be in the same AH main category. Cheaper eggs per piece now appear under the suggestion, and Prefer organic suggests the organic eggs. Other ingredients such as milk, rice, olive oil, butter and yoghurt also get more options; buttermilk does not count as milk, and apple sauce "0% sugar" does not count as sugar. For chicken and turkey breast the check list now suggests fresh meat instead of cold cuts: at Albert Heijn fresh meat comes before cold cuts when a single piece of fresh meat names the ingredient; ham and roast beef stay cold cuts.*

- **Bij eieren staan er weer andere eieren onder het voorstel, en *Liever biologisch* vindt biologische eieren** (issue [#223](https://github.com/helmerzNL/MealieShopper/issues/223)). De soort kwam uit de hele naam van het voorstel: bij *AH Witte vrije uitloopeieren M* moesten "witte", "vrije", "uitloopeieren" en "m" allemaal in een ander product staan, dus telde geen enkel ander ei mee.
  - **De soort volgt het ingrediënt** als het voorstel dat noemt. Bij eieren tellen dus alle eieren mee: *Natuurfarm de Boed Maïs scharreleieren* (€ 0,28 per ei) staat nu als *goedkoper per eenheid* onder het voorstel (€ 0,33), en met *Liever biologisch* is *AH Biologisch Eieren S M L* het voorstel. De soort komt uit het gewone voorstel, ook als het biologische product anders heet.
  - **De eimaten S, M en L zijn een maat**, net als een gewicht: *AH Witte vrije uitloopeieren L* is hetzelfde product als *… M*.
  - **Een los woord blijft een los woord.** Noemt het voorstel het ingrediënt als los woord (*AH Halfvolle melk*), dan telt een samenstelling niet mee: karnemelk is geen melk. Noemt het voorstel het als deel van een woord (*uitloopeieren*), dan tellen andere samenstellingen wel mee (*scharreleieren*).
  - **Bij Albert Heijn ook dezelfde hoofdcategorie.** *AH Appelmoes 0% suiker toegevoegd* noemt suiker, maar staat bij AH niet bij de suiker; zo'n product komt er niet bij. Vleeswaren en vers vlees blijven daardoor ook gescheiden. Picnic, Jumbo en Crisp geven geen categorie; daar telt alleen het ingrediënt.
  - Noemt het voorstel het ingrediënt niet (*Milner Jong belegen* bij kaas), dan blijft het zoals het was: de soort van het voorstel.
  - **Ook bij andere ingrediënten meer keus.** Getoetst tegen de echte zoekresultaten van AH: bij melk, rijst, olijfolie, roomboter en yoghurt staan er nu meer opties van dezelfde soort; bij rundergehakt, spaghetti, ui en kaas verandert niets.
- **Bij kipfilet rauwe kipfilet, geen vleeswaren** (issue [#226](https://github.com/helmerzNL/MealieShopper/issues/226)). Bij "kipfilet" stelde de controlelijst *AH Biologisch Kipfilet ca. 100 g* voor: plakjes voor op brood. AH zet bij "kipfilet" zelf vleeswaren en multipacks bovenaan, en *AH Biologisch Kipfilet* heet precies "kipfilet". De rauwe *AH Scharrel kipfilet 2 stuks* stond pas op plek 11.
  - **Nu:** bij Albert Heijn gaat vers vlees (AH's categorie *Vlees*) voor vleeswaren (*Vleeswaren*), als een los stuk vers vlees het ingrediënt noemt. Zo wordt kipfilet *AH Scharrel kipfilet 2 stuks*, kalkoenfilet *AH Kalkoenfilet schnitzel* en speklapjes *AH Speklapjes à la minute*.
  - Ham, rosbief, salami, fricandeau en ontbijtspek koop je als vleeswaren: daar noemt geen los stuk vers vlees het ingrediënt, dus daar verandert niets. Getoetst tegen de echte zoekresultaten van AH voor 34 ingrediënten: alleen bij kipfilet, kalkoenfilet, speklapjes en gerookte kipfilet verandert het voorstel.
- **Ontwikkeling**: `test_offers_stores` faalde af en toe met `OSError: Directory not empty`. Na een synchronisatie startte de test een echte thread die foto's in de tijdelijke map zette terwijl die werd opgeruimd (sinds de afbeeldingencache uit 3.2.0). Die tests zetten `image_cache.prefetch_async` nu stil, zoals de andere tests al deden.
- Issues: [#223](https://github.com/helmerzNL/MealieShopper/issues/223) en [#226](https://github.com/helmerzNL/MealieShopper/issues/226). Pull request: [#225](https://github.com/helmerzNL/MealieShopper/pull/225).

## v3.3.2

### Opgelost in 3.3.2

*English summary: Import old purchases no longer skips an order that is in your account without products. Old Jumbo orders (January 2022, for example) are listed under Orders as "products not fetched yet", because Jumbo refuses their details; Check called such an order "is already in your orders" or "your account already has an order on this day", so its products ended up nowhere. Only an order from your account with products now counts as a duplicate, by order number and by date, for all stores. The imported order then shows under Orders, in the Price history and in the purchase search. If MealieShopper fetches the products from the store later after all, the imported version drops out again, so nothing counts twice. An order from your account with products still blocks the import.*

- **Oude aankopen importeren: een bestelling zonder producten in je account telt niet meer als dubbel** (issue [#222](https://github.com/helmerzNL/MealieShopper/issues/222)). Oude Jumbo-bestellingen (bijvoorbeeld van januari 2022) staan bij **Bestellingen** als *producten nog niet opgehaald*, omdat Jumbo de details ervan weigert. **Controleren** zei bij zo'n bestelling *staat al in je bestellingen* of *op deze dag staat al een bestelling in je account*, en **Importeren** bleef uit; de producten kwamen zo nergens terecht, niet uit je account en niet uit de import.
  - Alleen een bestelling uit je account **met** producten telt nu als dubbel, op bestelnummer en op datum. Dat geldt voor alle winkels (AH, Jumbo, Picnic en Crisp).
  - De ingelezen bestelling staat daarna bij **Bestellingen**, in de **Prijsgeschiedenis** en in het zoeken op aankopen.
  - Haalt MealieShopper de producten later alsnog bij de winkel op, dan valt de ingelezen versie op die drie plekken weer weg, zodat niets dubbel telt.
  - Een bestelling uit je account met producten houdt de import nog steeds tegen.
- **Handleiding**: de regel voor dubbele bestellingen bij *Oude aankopen importeren*.
- Issue: [#222](https://github.com/helmerzNL/MealieShopper/issues/222). Pull request: [#224](https://github.com/helmerzNL/MealieShopper/pull/224).

## v3.3.1

### Opgelost in 3.3.1

*English summary: for eggs ("eieren") the check list at Albert Heijn suggested "AH Nederlandse aardbeien" (strawberries), and no eggs were in the drop-down. AH searches broadly and also finds "aardbeien" for "eieren". It does put eggs on top, but nearly all as multipacks, and since 2.87.1 MealieShopper puts a single product before a multipack, so the strawberries, liquid egg white and strawberry yoghurt moved up and the first single box of eggs ended up sixth. Now, when AH puts a multipack of the ingredient on top (before its first single product), the products whose name contains the ingredient come first, also as part of a word ("uitloopeieren"): for "eieren" that is "AH Witte vrije uitloopeieren M". Otherwise AH's order stays as it was; whether the ingredient is in the product name still does not count. Checked against AH's real results for 196 ingredients: only "eieren" gets another suggestion. This applies to the check list, Search for another product, `POST /api/ah/cart` for a line without a product and the MCP tool `search_products` (at Albert Heijn).*

- **Bij "eieren" stelt de controlelijst weer eieren voor.** Bij Albert Heijn stond *AH Nederlandse aardbeien* als voorstel voor eieren, en in de keuzelijst stonden geen eieren. AH zoekt ruim en vindt bij "eieren" ook "aardbeien". De eieren zet AH wel bovenaan, maar bijna allemaal als multipack ("4-pack"), en sinds 2.87.1 komt bij MealieShopper een los product vóór een multipack. Zo schoven de aardbeien, het vloeibare eiwit en de aardbeiyoghurt naar boven en stond de eerste losse doos eieren pas op plek 6.
  - **Nu:** zet AH bovenaan (vóór het eerste losse product) een multipack van het ingrediënt, dan komen de producten waarvan de naam het ingrediënt noemt eerst, ook als deel van een woord ("uitloopeieren", "scharreleieren"). Bij "eieren" is het voorstel *AH Witte vrije uitloopeieren M*, en in *Zoek een ander product* staan bovenaan vijf dozen eieren.
  - **Verder blijft de volgorde van AH**, zoals sinds 2.87.1: of het ingrediënt in de productnaam staat, telt alleen in het geval hierboven. Kaas van Milner heet "Jong belegen 35+ plakken" en blijft voor "Kruiden roomkaas" staan.
  - **Getoetst** tegen de echte zoekresultaten van AH voor 196 ingrediënten: alleen bij "eieren" verandert het voorstel. In de eerste vijf van *Zoek een ander product* schuift verder alleen iets bij "thee" (theesoorten met "thee" in de naam vóór "Clipper Warming pumpkin spice") en bij "bier" (alleen multipacks onderling).
- Geldt voor de controlelijst van het weekmenu (met en zonder *Liever biologisch*), *Zoek een ander product*, `POST /api/ah/cart` voor een regel zonder product en de MCP-tool `search_products` (bij Albert Heijn).
- **Handleiding**: de volgorde van de zoekresultaten bij Albert Heijn, onder *Weekmenu naar je mandje*.
- Issue: [#220](https://github.com/helmerzNL/MealieShopper/issues/220). Pull request: [#221](https://github.com/helmerzNL/MealieShopper/pull/221).

## v3.3.0

### Nieuw in 3.3.0

*English summary: the week menu can now prefer organic products. Next to the basket button is a Prefer organic switch; when it is on, the check list suggests an organic product of the same kind for each ingredient, when the store has one (for minced beef "AH Biologisch Rundergehakt", not the organic meatballs). Albert Heijn's own organic property counts, and otherwise "biologisch", "bio" or "organic" in the name or brand (also at Picnic, Jumbo and Crisp). Without an organic product in the results, MealieShopper searches once more with "biologisch" in front; if that finds nothing either, the regular product stays with the note "No organic option found". The regular product stays in the drop-down as cheaper per unit, and organic products are marked organic. Every user has their own switch, kept in the profile and part of the backup. Reload the page once after updating.*

- **Liever biologisch** in het weekmenu (issue [#218](https://github.com/helmerzNL/MealieShopper/issues/218)). Naast de knop van het mandje staat de schakelaar *Liever biologisch*. Staat hij aan, dan stelt de controlelijst per ingrediënt eerst een biologisch product van dezelfde soort voor, als de winkel het heeft.
  - **Wat telt als biologisch:** bij Albert Heijn het eigen kenmerk *biologisch* van AH ("AH Biologisch Spaghetti" heeft het), en verder, ook bij Picnic, Jumbo en Crisp, "biologisch", "bio" of "organic" in de naam of het merk. Niet elk biologisch product van AH heeft het kenmerk: "AH Biologisch Rundergehakt balletjes" niet.
  - **Welk product:** het eerste biologische product van dezelfde soort uit de zoekresultaten, en eerst precies dezelfde soort: bij rundergehakt "AH Biologisch Rundergehakt", niet de biologische gehaktballetjes. Is het voorgestelde product al biologisch, dan blijft het staan.
  - **Niet tussen de resultaten:** dan zoekt MealieShopper voor dat ingrediënt één keer op "biologisch rundergehakt". Geeft ook dat niets (of lukt die zoekopdracht niet), dan blijft het gewone product staan met de melding *Geen biologische variant gevonden*.
  - **De gewone variant blijft te kiezen:** in de keuzelijst staat hij als *goedkoper per eenheid*, zodat je ziet wat biologisch kost. Is het gekozen product biologisch, dan wijst de regel *Per kg goedkoper* alleen op een goedkopere biologische optie.
  - Biologische producten hebben in de keuzelijst het woord *biologisch*, ook bij *Zoek een ander product*.
  - **Per gebruiker bewaard:** de schakelaar staat in je profiel en blijft zoals je hem achterliet. Hij gaat mee in de back-up (een nieuwe kolom in het profiel; een oudere back-up terugzetten kan nog steeds, de schakelaar staat dan uit).

### Gewijzigd in 3.3.0

*English summary: "biologisch" no longer counts as part of the kind of product, so Compare pack sizes and the check list see "AH Biologisch rundergehakt" and "AH Rundergehakt" as the same kind.*

- **"Biologisch" hoort niet bij de soort.** *Vergelijk verpakkingen* en de controlelijst zien "AH Biologisch rundergehakt" en "AH Rundergehakt" nu als hetzelfde soort product. Vergelijk je een biologisch product, dan staat de gewone variant er dus ook bij (als *zelfde soort*).
- **API**: `PUT /api/profile/preferences` met `{"preferOrganic": true}`; `GET /api/auth/status` en `GET /api/profile` geven `preferOrganic`. `POST /api/<winkel>/cart/preview` neemt `"organic": true` (alleen samen met `"compare": true`) en geeft per regel `organic` en `organicMissing`, en per product `organic`.
- **Handleiding**: *Liever biologisch* onder *Weekmenu naar je mandje*.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Issue: [#218](https://github.com/helmerzNL/MealieShopper/issues/218). Pull request: [#219](https://github.com/helmerzNL/MealieShopper/pull/219).

## v3.2.0

### Nieuw in 3.2.0

*English summary: product photos of the stores (AH, Jumbo, Picnic, Crisp) no longer load straight from the store. The app fetches each photo once, as a browser would, keeps it for good in the `images` folder of the data folder and serves it from there (`GET /api/images/<token>`). A nightly round fills in what is missing at a calm pace, a small round runs after every sync of orders or receipts, and a 403 or 429 pauses that store for twelve hours. Receipt lines that are linked to a webshop product now carry its photo, and the nightly task also links receipt lines (it only did so after a manual sync). The cache is not part of the backup and is removed by a factory reset. `MEALIESHOPPER_IMAGE_PREFETCH=false` turns the background rounds off. The container now serves with four gunicorn threads.*

- **Winkelfoto's uit de eigen cache.** De productfoto's van AH, Jumbo, Picnic en Crisp (bestellingen, het mandje, aanbiedingen, de prijsgeschiedenis, zoekresultaten en recepten) laadt de browser niet meer rechtstreeks van de winkel. MealieShopper haalt elke foto één keer op, zoals een browser dat zou doen, bewaart het origineel voor altijd in de map `images` van de data-map en serveert hem daarna van schijf (`GET /api/images/<token>`, het token is het winkeladres in base64). Een foto van een andere host (Mealie, een maaltijdbox) blijft zoals hij was.
- **Rustig bij de winkel.** Hooguit twee verzoeken tegelijk, een korte pauze tussen twee verzoeken aan dezelfde winkel en in de achtergrond twee tot vijf seconden. Geeft een winkel een 403 of 429, dan wacht de cache twaalf uur met die winkel. Een foto die niet lukt, probeert hij na een dag opnieuw, vanaf drie keer na een week en vanaf tien keer niet meer.
- **De nachtelijke taak vult de cache aan**, na de aanbiedingen: hooguit 300 foto's per nacht, de nieuwste eerst. Na elke synchronisatie van bestellingen of kassabonnen volgt een kleine ronde (40), zodat de foto's er al staan voordat je een bestelling opent. Elke ronde staat in het auditlog. `MEALIESHOPPER_IMAGE_PREFETCH=false` zet de rondes uit. Op afroep ophalen blijft werken.
- **Foto's op de kassabon.** Een regel die aan een webwinkelproduct gekoppeld is, krijgt de foto van dat product mee (`product.image` in `GET /api/ah/receipts/<id>`). Oude koppelingen zonder foto vult de nachtelijke taak aan, 25 per verzoek.
- **Fix: de nachtelijke taak koppelt nu ook de kassabonregels.** Dat gebeurde alleen na een handmatige synchronisatie van de aanbiedingen. De geplande run sloeg het over.
- **Instellingen > Systeem** toont hoeveel foto's er in de cache staan en hoeveel ruimte dat is. De cache gaat niet mee in de back-up (hij wordt opnieuw opgebouwd) en verdwijnt bij het terugzetten van de fabrieksinstellingen.
- **Container**: gunicorn draait met vier threads (`--threads 4`), zodat een foto die nog bij de winkel vandaan moet de rest van de app niet ophoudt.
- **Handleiding en README**: de foto's en `MEALIESHOPPER_IMAGE_PREFETCH`.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`). Ververs de pagina één keer.
- Pull request [#217](https://github.com/helmerzNL/MealieShopper/pull/217), bouwt op [#216](https://github.com/helmerzNL/MealieShopper/pull/216).

## v3.1.0

### Nieuw in 3.1.0

*English summary: on a wide screen (from 1024px) the side menu now lists the parts of each section underneath it: Week menu and Find recipes under Recipes, Offers and Price history under Offers, and Recipes, Lists and Orders under Stores (for the chosen store), so one click opens Orders. The bar of sub-tabs above the page disappears; only a choice in it (the store under Stores and Offers) stays. The bar returns when you collapse the menu to icons, below 1024px, or when the tree does not fit the height of the screen. On a receipt, a line now links to the price history of the web shop product its article number is linked to (by id, pinned), instead of a search on the abbreviated receipt name that found nothing for DAVITAMON or COTTAGE CH. Opening a receipt looks up the lines that are not linked yet (at most five searches at AH, within the existing budget and pause) and redraws the receipt with the new links; a line AH does not know stays plain text. Under Stores > Orders your upcoming and past orders, imported orders and receipts now share one table (date, kind, status, products, amount), newest first, with filters above it: checkboxes for orders and receipts, an amount from and to, and the search field, which searches the products in your orders and receipts and shows only the purchases with that product, with the matching lines in the Products column. On a wide screen the table fills the rest of the screen and scrolls in its own box, and the receipt itself uses the full width and height of its frame. In the Price history the list of found products on the left now uses the full height of the screen (it was 420px) and stays put while you look at the product on the right. Reload the page once after updating.*

- **Zijmenu met de onderdelen eronder.** Op een breed scherm (vanaf 1024px) staan onder elk onderdeel van het zijmenu zijn tabbladen: *Weekmenu* en *Recepten zoeken* onder **Recepten**, *Aanbiedingen* en *Prijsgeschiedenis* onder **Aanbiedingen**, en *Recepten*, *Lijstjes* en *Bestellingen* onder **Winkels** (van de gekozen winkel). Eén klik op *Bestellingen* is genoeg. De tabbladenbalk boven de pagina verdwijnt dan; alleen een keuze erin (de winkel bij Winkels en Aanbiedingen) blijft staan. De balk komt terug als je het menu inklapt tot pictogrammen, onder 1024px, of als de boom niet in de hoogte van het scherm past.
- **Kassabon: regels linken naar het product.** Een regel op de bon linkt naar de prijsgeschiedenis van het webwinkelproduct waaraan het artikelnummer gekoppeld is (op id, vastgepind), niet meer naar een zoekopdracht op de afgekorte bonnaam, die bij *DAVITAMON* of *COTTAGE CH* niets vond. Bij het openen van een bon zoekt MealieShopper de regels op die nog niet gekoppeld zijn (hooguit vijf zoekopdrachten bij AH, binnen het bestaande budget en dezelfde pauze als de nachtelijke taak) en tekent de bon opnieuw met de links die erbij kwamen. Een regel die AH niet kent, blijft gewone tekst.
- **Prijsgeschiedenis: de productlijst zo hoog als het scherm.** Op een breed scherm gebruikt de lijst met gevonden producten links de hele schermhoogte (was 420px) en blijft hij staan terwijl je het product rechts bekijkt.
- **Bestellingen en kassabonnen in één tabel.** Onder **Winkels > Bestellingen** staan je aankomende en afgelopen bestellingen, de ingelezen bestellingen en je kassabonnen samen in één tabel (datum, soort, status, producten, bedrag), de nieuwste bovenaan; een klik op een rij opent de bestelling of de bon. Erboven staan de filters: vinkjes voor *Bestellingen* en *Kassabonnen*, een bedrag *van* en *tot*, en het zoekveld. Dat zoekt op de producten in je bestellingen en bonnen en laat alleen de aankopen met dat product zien, met de gevonden regels in de kolom Producten. Het zoeken gebeurt op de server, een kwart seconde na de laatste toets; de andere filters werken direct in de browser. Op een breed scherm vult de tabel de rest van het scherm en scrolt hij in zijn eigen vak. Op een telefoon vallen de kolommen Soort en Status weg.
- **De bon zelf ruimer.** De kassabon gebruikt de hele breedte van het kader en op een breed scherm ook de hoogte.
- **API**: `GET /api/ah/purchases/search` en `GET /api/jumbo/purchases/search` nemen `limit` (een getal of `all`), en de AH-zoekopdracht geeft `unsearched` (bonnen en bestellingen waarvan de producten nog niet zijn opgehaald). `GET /api/ah/receipts/search` neemt ook `limit`. `GET /api/ah/receipts/<id>` geeft per regel `product` (`id` en `title` van het webwinkelproduct, of `null`); `POST /api/ah/receipts/<id>/products` zoekt de niet-gekoppelde regels op en geeft de bon terug met `products` (het resultaat van het zoeken, of `null` als dat misging).
- **Handleiding**: het zijmenu en de links op de kassabon.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request [#216](https://github.com/helmerzNL/MealieShopper/pull/216).

## v3.0.0

### De stabiele release in het kort: alles sinds 2.97.1

*English summary: version 3.0.0 is the stable release after 2.97.1 and brings what came out as the 2.98.0 beta: bulk import of supermarket recipes into Mealie. Under Stores you select saved Albert Heijn recipes or recipes from your Jumbo lists (at most 50 at a time), or, as the administrator, import a whole AH recipe group or Jumbo list in one go. Every recipe gets the store as tag in Mealie, and the group or list as category; a recipe that is already in Mealie is skipped and only gets what is missing. The import runs in the background with a report per recipe and stops by itself when a store refuses the requests. 3.0.0 is a new major version, but nothing breaks: no changes to Compose or .env files, no new dependencies and no database changes. When updating: pull the latest image (or update the Home Assistant add-on) and reload the page once. For tags and categories, the Mealie user of your API token needs the right to organise.*

- **Versie 3.0.0.** Een nieuwe hoofdversie, met de bulkimport als grootste nieuwe functie. Er breekt niets: wat met 2.97.1 werkte, werkt zonder aanpassingen verder.
- **Recepten in bulk importeren in Mealie** (2.98.0). Bij **Winkels** zet je je bewaarde AH-recepten en je receptenlijsten van Jumbo in één keer in Mealie:
  - met **Selecteren** vink je recepten aan (hooguit 50 tegelijk) en kies je **Importeer N in Mealie**; dat mag iedereen die ook één recept mag importeren;
  - met **Hele groep importeren** of **Hele lijst importeren** zet de beheerder een hele AH-receptgroep (of alle bewaarde recepten) of een hele Jumbo-lijst in Mealie;
  - elk recept krijgt in Mealie de tag *Albert Heijn* of *Jumbo*, en uit een groep of lijst ook de categorie met die naam; wat al in Mealie staat, wordt overgeslagen en krijgt alleen de tag en categorie die ontbreken;
  - de import loopt op de achtergrond, met een venster met de voortgang en een verslag per recept, en stopt vanzelf als AH of Jumbo de verzoeken weigert. Per keer komen hooguit 250 nieuwe recepten mee; opnieuw starten gaat verder waar de vorige stopte.
- **Jumbo-recepten op nummer in Mealie** (2.98.0): inplannen van een Jumbo-recept dat al in Mealie staat, zet het er niet nog eens in.
- **Minder aandringen bij een weigering** (2.98.0): AH en Jumbo proberen bij een 403 of 429 niet meer andere varianten van hetzelfde verzoek.
- **Goed om te weten bij het updaten:**
  - geen wijzigingen in Compose- of `.env`-bestanden, geen nieuwe afhankelijkheden en geen nieuwe tabellen in de database;
  - haal het image `latest` (of `3.0.0`) binnen, of werk de Home Assistant-add-on bij, en ververs daarna de pagina één keer;
  - voor de tags en categorieën moet de Mealie-gebruiker van je API-token mogen organiseren (in Mealie bij de gebruiker: *Organiseren*). Zonder dat recht worden de recepten wel geïmporteerd, met een melding.
- De volledige tekst per versie staat hieronder bij v2.98.0.
- Pull requests: [#213](https://github.com/helmerzNL/MealieShopper/pull/213) en [#214](https://github.com/helmerzNL/MealieShopper/pull/214).

## v2.98.0

### Nieuw in 2.98.0

*English summary: you can now put the recipes of a supermarket into Mealie in bulk. Under Stores, Albert Heijn (your saved recipes) and Jumbo (your recipe lists), Select gives every card a box; tick recipes (at most 50) and choose Import N recipes into Mealie. The administrator can also import a whole AH recipe group (or all saved recipes) or a whole Jumbo list in one go. In Mealie every recipe gets the tag Albert Heijn or Jumbo, and from a group or list also the category with that name; MealieShopper creates a missing tag or category (the Mealie user needs the right to organise). A recipe that is already in Mealie is not imported again; it only gets the missing tag and category. The import runs in the background with a progress window and a report per recipe, survives a page reload, can be stopped, and stops by itself when AH or Jumbo refuse the requests (403 or 429), when Mealie cannot look up existing recipes, or after five failures in a row. Each run fetches at most 250 new recipes; start again for the rest. Also: Mealie now recognises a Jumbo recipe by its number, so planning a Jumbo recipe that is already in Mealie no longer imports it twice, and AH and Jumbo stop retrying at once on a 403 or 429. Reload the page once after updating.*

- **Recepten in bulk importeren in Mealie.** Bij **Winkels** zet je je bewaarde AH-recepten en je receptenlijsten van Jumbo in één keer in Mealie.
  - **Selecteren** boven de recepten geeft elke kaart een vakje; tik op een kaart om haar te kiezen, of kies **Alles selecteren**. Onderaan staat een balk met **Importeer N in Mealie**. Je kiest er hooguit 50 tegelijk; dat mag iedereen die ook één recept mag importeren.
  - **Hele groep importeren** (AH; met *Alle recepten* gekozen: **Alle recepten importeren**) en **Hele lijst importeren** (Jumbo) zetten alles uit die groep of lijst in Mealie. Dat mag alleen de beheerder.
  - Een venster vraagt eerst om een bevestiging, met het aantal, de tag en categorie en een schatting van de duur. Daarna loopt de import op de achtergrond: het venster toont de voortgang en per recept *Geïmporteerd*, *Stond al in Mealie* of *Mislukt* met de reden, met een link naar Mealie. Gesloten of na het herladen staat boven de recepten *Import naar Mealie loopt*. **Stoppen** mag wie de import startte en de beheerder. Er loopt één import tegelijk.
- **Tag en categorie in Mealie.** Elk recept krijgt de tag *Albert Heijn* of *Jumbo*, en uit een groep of lijst ook de categorie met die naam (niet uit *Alle recepten* en de Jumbo-*Favorieten*). Een tag of categorie die nog niet bestaat, maakt MealieShopper aan; daarvoor moet de Mealie-gebruiker van je token mogen organiseren. Mag dat niet, dan gaat de import door zonder tag, met een melding.
- **Niets dubbel, rustig voor de winkels.** Een recept dat al in Mealie staat (zelfde bron), wordt niet opnieuw geïmporteerd en krijgt alleen de tag en categorie die ontbreken. Kan Mealie niet zoeken wat er al staat, dan stopt de import. Tussen twee recepten zit anderhalve seconde; bij een 403 of 429 van AH of Jumbo, vijf mislukte recepten achter elkaar of een geweigerd Mealie-token stopt de import. Per keer worden hooguit 250 nieuwe recepten opgehaald (wat er al staat telt niet mee); opnieuw starten gaat verder waar de vorige stopte, ook na een herstart. De eenheden en ingrediënten van Mealie worden één keer per import opgehaald in plaats van bij elk recept.
- **Jumbo-recepten op nummer in Mealie.** MealieShopper herkent een Jumbo-recept in Mealie nu aan het receptnummer in de link, zoals al bij Allerhande. Inplannen van een Jumbo-recept dat al in Mealie staat, zet het er dus niet nog eens in, ook als de naam in de link anders is.
- **Minder aandringen bij een weigering.** Een 403 of 429 van de GraphQL van AH probeert geen andere variant van de vraag meer, en ook het ophalen van het anonieme AH-token geeft de status door. Bij Jumbo probeert een 403 of 429 niet meer alle twaalf combinaties van clientnaam en verzoek; een 429 telt nu ook als weigering.
- **API**: `POST /api/mealie/bulk-import` (een selectie: `store`, `ids` en optioneel `group` voor de categorie), `POST /api/mealie/bulk-import/group` (een hele groep of lijst, alleen de beheerder), `GET /api/mealie/bulk-import` (de voortgang, met codes voor fouten en waarschuwingen) en `POST /api/mealie/bulk-import/cancel`. Per import staat er één regel in het auditlog (*Mealie bulk import*). Er is geen MCP-tool voor.
- **Handleiding**: het nieuwe kopje *In bulk importeren in Mealie*, met twee nieuwe screenshots.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#213](https://github.com/helmerzNL/MealieShopper/pull/213).

## v2.97.1

### Beveiliging in 2.97.1

*English summary: Pillow is updated from 12.3.0 to 13.0.0. The new version fixes 38 security issues in reading and writing images. Two are critical: native image memory that could be reused after a mode change (CVE-2026-96639, GHSA-v9pv-5gc8-w2m6), and a buffer overflow in the JPEG decoder (GHSA-5x8w-8jj5-q52h, GHSA-8g5g-8vmr-9782). Among the others are out-of-bounds reads and writes for TIFF, WMF/EMF and the bundled OpenJPEG, an endless loop when replacing Exif headers (CVE-2026-96643), and decompression bomb checks that could be bypassed. MealieShopper uses Pillow for the Saved images in the Price history: it opens, rotates and shrinks the product images it downloads from the stores, and reads them again when you restore a backup. The container installs the new version by itself; without the container, run pip install -r requirements.txt again. Pillow 13 no longer supports Python 3.10 (the container uses Python 3.12).*

- **Pillow 12.3.0 → 13.0.0.** De nieuwe versie lost 38 beveiligingsproblemen op bij het lezen en schrijven van afbeeldingen. Twee zijn kritiek: geheugen van een afbeelding dat na een moduswissel opnieuw gebruikt kon worden (CVE-2026-96639, GHSA-v9pv-5gc8-w2m6), en een bufferoverloop in de JPEG-decoder (GHSA-5x8w-8jj5-q52h, GHSA-8g5g-8vmr-9782). Verder onder meer lezen en schrijven buiten het geheugen bij TIFF, WMF/EMF en de meegeleverde OpenJPEG, een eindeloze lus bij het vervangen van Exif-kopjes (CVE-2026-96643) en controles op decompressiebommen die te omzeilen waren. Alles staat in de [release notes van Pillow 13.0.0](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html#security).
- MealieShopper gebruikt Pillow voor de **Bewaarde afbeeldingen** in de Prijsgeschiedenis: het opent, draait en verkleint de productafbeeldingen die het bij de winkels ophaalt, en leest ze opnieuw bij het terugzetten van een back-up. Daar komen dus afbeeldingen van buiten binnen.
- **Bij het updaten:** de container installeert de nieuwe versie vanzelf. Draai je MealieShopper zonder container, voer dan `pip install -r requirements.txt` opnieuw uit. Pillow 13 werkt niet meer met Python 3.10; de container gebruikt Python 3.12.
- Pull requests: [#208](https://github.com/helmerzNL/MealieShopper/pull/208) en [#210](https://github.com/helmerzNL/MealieShopper/pull/210).

## v2.97.0

### Nieuw in 2.97.0

*English summary: the week menu now starts on the first day of the week of your household in Mealie, the same day as the meal planner in Mealie (from Mealie 3.30). MealieShopper reads it from Mealie (GET /api/households/preferences), at most once every ten minutes and at once with Check link; if Mealie is not linked or does not answer, the week starts on Monday as before. Mealie's default is Sunday, so the owner can also pick a fixed day under Settings, Link, Mealie, First day of the week (As in Mealie is the default). The choice applies to everyone, to the MCP tool get_week_menu and goes into the backup. Offers keep AH's Bonus week from Monday to Sunday and show the Bonus week that overlaps the chosen week most. Reload the page once after updating.*

- **De week begint op de eerste dag uit Mealie.** In Mealie stel je bij je huishouden de eerste dag van de week in, en sinds Mealie 3.30 begint de weekplanner daar ook op. Het weekmenu in MealieShopper volgt nu dezelfde dag, ook bij het bladeren door de weken en na een wijziging.
  - MealieShopper vraagt de dag aan Mealie (`GET /api/households/preferences`, veld `firstDayOfWeek`; elke token mag dat lezen) en onthoudt hem tien minuten. **Koppeling controleren** haalt hem meteen opnieuw op.
  - Is Mealie niet gekoppeld of niet bereikbaar, dan begint de week op maandag, zoals tot nu toe.
- **Eerste dag van de week kiezen** (alleen de beheerder): onder **Instellingen > Koppelen > Mealie** staat de keuzelijst *Eerste dag van de week*, met *Zoals in Mealie* (standaard, met de dag die Mealie nu geeft) of een vaste dag. Handig omdat Mealie standaard op zondag staat. De keuze geldt voor iedereen en gaat mee in de back-up (`WEEK_START`).
- **MCP**: `get_week_menu` begint zonder `start_date` op dezelfde eerste dag van de week in plaats van op maandag.
- **Aanbiedingen** houden de bonusweek van AH, van maandag t/m zondag. Begint het weekmenu op een andere dag, dan hoort er de bonusweek bij die er het meest mee overlapt (de week waarin de middelste dag valt): bij za 7 t/m vr 13 nov is dat ma 9 t/m zo 15 nov. Met maandag als eerste dag verandert er niets.
- **API**: `GET /api/mealie/status` geeft `weekStart` (`day`, 0 = zondag t/m 6 = zaterdag, met `source`: `setting`, `mealie` of `default`); `GET` en `PUT /api/server-settings` hebben `weekStart` (leeg = zoals in Mealie, of 0 t/m 6).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#204](https://github.com/helmerzNL/MealieShopper/pull/204).

## v2.96.0

### Nieuw in 2.96.0

*English summary: Import old purchases is now also on the Orders page of every store (Albert Heijn, Jumbo, Picnic and Crisp), and the orders you import from your confirmation emails are listed among your past orders on their date, like any other order. You can open them for their products (at AH and Jumbo with their own address), and the purchase search on the Orders page finds them too. An imported order has an archive box in front of its date and, for the administrator, a bin that removes the whole order (it then no longer counts in the Price history either). An imported order that the app later also fetches from your account is not listed twice. Also in this version: when you search the Price history for a name that is on a receipt but not linked yet, the app looks that name up at AH right away (#202). Reload the page once after updating.*

- **Oude aankopen importeren ook bij Bestellingen.** De knop uit de Prijsgeschiedenis staat nu ook op het tabblad **Bestellingen** van elke winkel (Albert Heijn, Jumbo, Picnic en Crisp) en opent dezelfde dialoog, voor de winkel die je daar bekijkt. Alleen de beheerder ziet de knop, net als in de Prijsgeschiedenis.
- **Ingelezen bestellingen bij de afgelopen bestellingen.** Wat je uit je bevestigingsmails inleest, staat tussen je **Afgelopen bestellingen** op de dag van de bestelling, met *Geïmporteerd*, het totaal en het aantal producten.
  - Een ingelezen bestelling werkt als elke andere: open hem voor de producten (met wat je per stuk betaalde en *Bonus* bij korting; de naam linkt naar de Prijsgeschiedenis). Bij AH en Jumbo heeft hij een eigen adres (`/stores/ah/imported/<bestelnummer>`), zodat herladen en een bladwijzer werken.
  - Het zoekveld bij Bestellingen (AH en Jumbo) vindt ook de producten uit ingelezen bestellingen.
  - Een **archiefdoos** voor de datum laat zien dat de bestelling is ingelezen. Met de **prullenbak** op de kaart verwijdert de beheerder de hele bestelling, na een bevestiging; ze telt dan ook niet meer mee in de Prijsgeschiedenis.
  - Haalt de app een ingelezen bestelling later toch uit je account (zelfde bestelnummer, of een bestelling op dezelfde dag), dan staat ze er maar één keer, net als in de Prijsgeschiedenis.
- **API en MCP**: `GET /api/imported-orders/<winkel>` en `GET /api/imported-orders/<winkel>/<ref>` geven de ingelezen bestellingen (voor iedereen die de bestellingen mag zien). `DELETE /api/prices/import` neemt `ref` om één bestelling te verwijderen (alleen de beheerder). `GET /api/ah/purchases/search`, `GET /api/jumbo/purchases/search` en de MCP-tool `search_ah_purchases` geven de ingelezen regels met `imported: true`.
- **Kassabonnen: namen meteen opzoeken** (#178, door wolph). Zoek je in de Prijsgeschiedenis op een naam die op een bon staat maar nog niet gekoppeld is (*redband*), dan zoekt MealieShopper die naam meteen bij AH op: hooguit twee zoekopdrachten, binnen dezelfde pauze en het uurbudget voor de hele app.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull requests: [#202](https://github.com/helmerzNL/MealieShopper/pull/202).

## v2.95.0

### Nieuw in 2.95.0

*English summary: the offers now include your personal AH offers, the Bonus Box from the AH app. When your AH account is linked, MealieShopper fetches the Bonus Box of this week and (from Friday) next week together with the regular bonus, every day and on Sync. Bonus Box offers are at the top of the AH offers with a label that says whether you activated them (green) or still have to activate them in the AH app (yellow); only an activated offer applies, and activating stays in the AH app because it can't be undone. A new switch, Bonus Box only, above the categories shows only your Bonus Box, with its own category counts, and is remembered in your browser; next to it you see how many you activated, or why there are none. If fetching the Bonus Box fails, the regular bonus is still updated and the Bonus Box from last time stays. The Bonus Box also counts in the recipe suggestions and is marked in the Price history. Also in this version: receipt lines are looked up per name at AH, with a second try and fewer requests (#200), and the pull request check moves a Dependabot pull request from main to beta instead of failing (#199). Reload the page once after updating.*

- **De AH Bonus Box bij de aanbiedingen.** Is je AH-account gekoppeld, dan haalt MealieShopper bij de aanbiedingen van Albert Heijn ook je persoonlijke aanbiedingen op: de Bonus Box uit de AH-app, van deze week en (vanaf vrijdag) van volgende week. Dat gaat mee met de dagelijkse synchronisatie en met `Aanbiedingen synchroniseren`.
  - De Bonus Box staat bovenaan de aanbiedingen van AH, met het label *Bonus Box · geactiveerd* (groen) of *Bonus Box · activeer in de AH-app* (geel). AH laat je er elke week een aantal activeren (5, met Premium 10) en alleen een geactiveerde aanbieding geldt. Activeren doe je zelf in de AH-app: dat kan niet ongedaan worden, dus MealieShopper doet het niet.
  - **Schakelaar Alleen Bonus Box** boven de categorieën: aan, dan zie je alleen je Bonus Box, met de categorieën en aantallen die daarbij horen. De keuze blijft bewaard in je browser. Ernaast staat hoeveel je er hebt geactiveerd, of waarom er geen zijn: geen AH-koppeling (dan staat de schakelaar uit), of het ophalen mislukte.
  - Mislukt alleen het ophalen van de Bonus Box, dan wordt de gewone bonus toch bijgewerkt en blijft de Bonus Box van de vorige keer staan; de fout staat bij de schakelaar en onder **Instellingen > Koppelen > Aanbiedingen** (*waarvan 3 uit je Bonus Box*).
  - Staat een product zowel in de gewone bonus als in je Bonus Box, dan staan beide acties in de lijst.
  - De Bonus Box telt mee in de receptvoorstellen. In de **Prijsgeschiedenis** staat een periode uit je Bonus Box als *Bonus Box*; staat een product in beide, dan telt daar de gewone bonus.
  - De routes zijn onofficieel en nog niet met een eigen antwoord gecontroleerd: eerst de GraphQL van de app (`bonusPromotions` met `filterSet: APP_BONUS_BOX`, met het gebruikerstoken), anders `GET bonuspage/v1/personal`. De AH-verkenner (`POST /api/ah/explore`) laat van beide zien hoeveel acties en producten er zijn en welke activatiestatussen AH geeft, zonder producten of prijzen; deel dat rapport als de Bonus Box niet verschijnt.
- **API en MCP**: `GET /api/offers` geeft per aanbieding `personal` en `activated`, en in `personal` of de winkel een Bonus Box heeft, of de koppeling er is, het aantal, hoeveel er geactiveerd zijn en een eventuele fout. `POST /api/offers/sync-all` en de status geven het aantal per winkel. De MCP-tool `get_price_history` geeft `personal` per aanbieding.
- **In de database**: de tabellen `offers` en `offer_history` krijgen de kolom `personal` (en `offers` ook `activated`), `offers_meta` de kolom `personal_error`. Een bestaande database krijgt ze bij de start; bestaande rijen zijn gewone aanbiedingen. Ze gaan mee in de back-up, en een oudere back-up zet gewoon terug.
- **Kassabonnen: echte producten per naam** (#178, door wolph). MealieShopper zoekt de namen op de bonnen per naam bij AH op en koppelt een artikelnummer alleen aan het product met precies dat nummer. Een afgekorte naam krijgt een tweede poging met het langste woord (*RB PICOLIENT* wordt *PICOLIENT*), en het gaat zuinig: hooguit 20 zoekopdrachten na een synchronisatie en 60 in de nacht, met een pauze bij een 403 of 429.
- **Pull request target**: een pull request van Dependabot naar `main` faalt niet meer, maar wordt door de check naar `beta` verzet (of gesloten als de update al in `beta` staat).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull requests: [#199](https://github.com/helmerzNL/MealieShopper/pull/199) en [#200](https://github.com/helmerzNL/MealieShopper/pull/200).

## v2.94.1

### De stabiele release in het kort: alles sinds 2.91.1

*English summary: this is the stable release after 2.91.1; it brings everything from 2.91.2, 2.92.0, 2.93.0, 2.94.0 and 2.94.1. Every screen has its own address, so reloading, bookmarks and back and forward in the browser work; the menu, tabs and store choice are real links (Ctrl+click opens a new tab), and screens link to each other (an offer to its price history, a purchase to its order or receipt, a product back to its price history). Price history gets the "Only bought before" switch, keeps the images of the products you bought (Saved images), and lets the owner import old purchases from order confirmation emails with the help of an AI assistant. A product on an AH receipt and the same product from an order are now one product, a receipt line no longer ends up at a completely different product, and the chart no longer runs to the year 2999. Your saved AH recipes come with their recipe groups. Compare pack sizes no longer shows its count in a red error box, and choosing another store in the Basket while the AH basket is still loading no longer shows an error. pyasn1 is updated to 0.6.5 (security fixes). When updating: the new dependency Pillow is installed by the container; without the container run pip install -r requirements.txt again. A reverse proxy must allow requests of about 5 MB for the largest import. Reload the page once after updating.*

- **Elk scherm een eigen adres** (2.93.0). Herladen, een bladwijzer en terug en vooruit in de browser werken zoals je verwacht: het weekmenu staat op `/recipes`, de prijsgeschiedenis op `/offers/history`, een bestelling op `/stores/ah/orders/<nummer>`, het mandje op `/cart`, enzovoort. Het menu, de tabs, de winkelkeuze en het gebruikersmenu zijn echte links: met Ctrl+klik (Cmd+klik op een Mac) of de middelste muisknop open je een scherm in een nieuw tabblad.
- **Schermen linken naar elkaar** (2.93.0): een aanbieding naar haar prijsgeschiedenis, een aankoop in de prijsgeschiedenis naar de bestelling of kassabon, en een product in een bestelling, het mandje of op een kassabon terug naar zijn prijsgeschiedenis.
- **De Prijsgeschiedenis is uitgebreid:**
  - de schakelaar **Alleen eerder gekocht** toont alleen de producten die je minstens één keer kocht (2.92.0);
  - van de producten die je kocht bewaart MealieShopper de afbeelding, met de naam van toen: onder **Bewaarde afbeeldingen** zie je of een product met een nieuwe naam of foto nog hetzelfde is (2.92.0);
  - met **Oude aankopen importeren** (alleen de beheerder) lees je bestellingen uit je bevestigingsmails in: een AI-assistent als Claude of ChatGPT zet ze om naar JSON, **Controleren** laat per bestelling zien of ze nieuw is, en niets telt dubbel (2.93.0).
- **Kassabon en bestelling: één product** (2.93.0). *BLOEMKOOL* op de kassabon en *AH Bloemkool* uit een bestelling stonden apart in de lijst; nu koppelt MealieShopper het artikelnummer op de bon aan het product bij AH. Een bonregel komt ook niet meer bij een heel ander product terecht (*AH ROOKWORST* bij een pizza), en de grafiek loopt niet meer tot het jaar 2999.
- **Receptgroepen bij je bewaarde AH-recepten** (2.94.0), net als de receptenlijsten bij Jumbo; de AI-assistent krijgt de MCP-tool `list_ah_recipe_groups`.
- **Opgelost**: *Vergelijk verpakkingen* toont de telling niet meer in een rood foutvak, en de uitleg boven Recepten zoeken noemt Crisp (2.91.2). Kies je in het Mandje een andere winkel terwijl het AH-mandje nog laadt, dan blijft het mandje van die winkel staan, zonder foutmelding (2.94.1).
- **Beveiliging** (2.94.1): pyasn1 0.6.5, zie hieronder.
- **Bij het updaten:**
  - **Nieuwe afhankelijkheid: Pillow** (2.92.0). De container installeert die vanzelf; draai je MealieShopper zonder container, voer dan `pip install -r requirements.txt` opnieuw uit.
  - Gebruik je een reverse proxy, laat die dan aanvragen van ongeveer 5 MB door, anders lukt de grootste import van oude aankopen niet.
  - Er gaat meer mee in de back-up: de bewaarde afbeeldingen, de ingelezen aankopen en de AH-receptgroepen. Maak na het updaten een nieuwe back-up als je die er ook in wilt hebben.
  - Ververs de pagina één keer, dan laadt de browser de nieuwe scripts en stijlen.

### Opgelost in 2.94.1

*English summary: in the Basket tab, choosing Picnic, Jumbo or Crisp while the AH basket was still loading or syncing showed "Cannot set properties of null (setting 'textContent')" in a red box, and the basket of the chosen store disappeared. The AH basket now only draws itself while AH is still chosen, and an error of the AH basket only shows at AH. Reload the page once after updating.*

- **Een andere winkel kiezen in het Mandje terwijl het AH-mandje nog laadt, geeft geen fout meer.** Het Mandje opent bij Albert Heijn en haalt dat mandje op (en synchroniseert het). Koos je in die paar seconden Picnic, Jumbo of Crisp, dan tekende het AH-mandje zich daarna alsnog, in een scherm dat er niet meer was: *Cannot set properties of null (setting 'textContent')* stond in een rood vak en het mandje van de gekozen winkel was weg. Nu tekent het AH-mandje alleen zolang AH gekozen is, en staat een fout van het AH-mandje alleen bij AH. De fout zat er al langer in; de screenshots vonden hem.
- `tests/test_basket_switch.py` draait het wisselen van winkel tijdens het laden na, in Node.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#197](https://github.com/helmerzNL/MealieShopper/pull/197).

### Beveiliging in 2.94.1

*English summary: pyasn1 is updated from 0.6.4 to 0.6.5. The new version fixes four ways in which crafted ASN.1 data could make decoding take quadratic CPU time (GHSA-mm57-4v3p-rchx, GHSA-2qm8-x737-gmw5, GHSA-9m75-vc3g-4x39 and GHSA-fm9c-5rcf-2q5p). MealieShopper uses pyasn1 through py_webauthn, to read the certificates of passkeys.*

- **pyasn1 0.6.4 → 0.6.5.** De nieuwe versie lost vier manieren op waarop gemaakte ASN.1-gegevens het decoderen kwadratisch veel rekentijd konden laten kosten (GHSA-mm57-4v3p-rchx, GHSA-2qm8-x737-gmw5, GHSA-9m75-vc3g-4x39 en GHSA-fm9c-5rcf-2q5p). MealieShopper gebruikt pyasn1 via py_webauthn, om de certificaten van passkeys te lezen.
- Pull request: [#191](https://github.com/helmerzNL/MealieShopper/pull/191).

### Documentatie in 2.94.1

*English summary: the README (Dutch and English) and its screenshots are updated to 2.94. New screenshots show Only bought before with the Saved images of a product that was renamed, Import old purchases after Check, and a chosen AH recipe group; the screenshot of the saved AH recipes now shows the group list. Every screenshot was made again and now shows the address of the screen in the address bar of the window.*

- **README's en screenshots bijgewerkt tot 2.94**, in het Nederlands en het Engels. Nieuw:
  - `price-images`: de Prijsgeschiedenis met **Alleen eerder gekocht** aan, en de **Bewaarde afbeeldingen** van kipfilet, dat tegenwoordig *Scharrel kipfilet* heet en een nieuwe foto kreeg;
  - `price-import`: **Oude aankopen importeren** na **Controleren**, met per bestelling of ze nieuw is of waarom niet;
  - `recipe-groups`: je bewaarde AH-recepten met de receptgroep *Doordeweeks* gekozen.
- `recipes` toont nu de keuzelijst met *Alle recepten* en je receptgroepen.
- **Alle screenshots zijn opnieuw gemaakt** en tonen in de adresbalk van het venster het adres van het scherm (`/offers/history`, `/stores/ah/recipes`), want elk scherm heeft sinds 2.93.0 een eigen adres. Het screenshot-script (`scripts/screenshots/lib.js`) zet dat adres er zelf in.
- De README noemt bij *Wat kan het?* ook Alleen eerder gekocht, de bewaarde afbeeldingen, het importeren van oude aankopen en de receptgroepen.
- Pull request: [#197](https://github.com/helmerzNL/MealieShopper/pull/197).

## v2.94.0

### Nieuw in 2.94.0

*English summary: your saved AH recipes now come with their recipe groups, just like the recipe lists at Jumbo. Under Stores > Albert Heijn > Recipes a list next to the title shows All recipes and every recipe group you made in the AH app, each with its number of recipes; choosing a group shows only its recipes, in the group's own order, and searching works within the chosen group. The groups are fetched together with the recipes (Fetch from AH, Refresh) and kept in the local copy, so choosing a group never calls AH. A default group that holds all your recipes is not listed separately, because it is the same as All recipes. If fetching the groups fails, the recipes are still kept, without the list. The AI assistant gets the MCP tool list_ah_recipe_groups, and list_saved_ah_recipes takes a group. The groups are part of the backup. Reload the page once after updating.*

- **Receptgroepen bij je bewaarde AH-recepten**, zoals de receptenlijsten bij Jumbo. Onder **Winkels > Albert Heijn > Recepten** staat naast de titel een keuzelijst met *Alle recepten* en elke receptgroep die je in de AH-app maakte, elk met het aantal recepten.
  - Kies je een groep, dan zie je alleen de recepten daarvan, in de volgorde van die groep. Zoeken werkt binnen de gekozen groep; een lege groep meldt *Er staan geen recepten in deze groep.*
  - De groepen komen mee met `Ophalen bij AH` en `Vernieuwen` en staan in de lokale kopie: een groep kiezen vraagt niets aan AH.
  - Een standaardgroep met al je recepten staat er niet apart in, want die is gelijk aan *Alle recepten*. Zonder groepen is er geen keuzelijst.
  - Lukt het ophalen van de groepen niet (bijvoorbeeld als de recepten uit het kookboek komen), dan worden de recepten wel bewaard, zonder groepen.
  - MealieShopper las de groepen al (`recipeCollectionCategories`), maar voegde alle recepten samen tot één lijst. `ah.get_saved_recipe_groups()` geeft ze nu per groep; `GET /api/ah/recipes/saved` geeft `groups` (`id`, `name`, `isDefault`, `count`, `recipeIds`).
- **MCP**: de nieuwe tool `list_ah_recipe_groups` geeft je receptgroepen met het aantal recepten, en `list_saved_ah_recipes` neemt `group` (id of een deel van de naam, bijvoorbeeld *taarten*).
- **In de back-up**: de receptgroepen gaan mee (`ah_saved_recipe_groups` en `ah_saved_recipe_group_items`). Een oudere back-up zonder groepen zet gewoon terug.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#195](https://github.com/helmerzNL/MealieShopper/pull/195).

## v2.93.0

### Nieuw in 2.93.0

*English summary: every screen now has its own address (/recipes, /cart, /offers/history, /stores/ah/orders/<number>, /stores/ah/receipts/<id> and so on), so reloading, bookmarks and back and forward in the browser work. The menu, the tabs, the store choice and the user menu are real links: Ctrl+click (Cmd+click on a Mac) or the middle mouse button opens a screen in a new tab. Screens link to each other: an offer to its price history, a purchase in the price history to its order or receipt, and a product in an order, the basket or on a receipt back to its price history. In Price history the owner can import old purchases from order confirmation emails: copy the prompt, give it with the emails to an AI assistant such as Claude or ChatGPT, paste the JSON it returns and choose Check to see per order whether it is new or already there; only Import reads it in. An order that was already imported, or that the app knows from your account, is skipped, also when the app fetches it from your account later. Imported purchases are part of the backup and can be removed again per store. Reload the page once after updating.*

- **Elk scherm een eigen adres.** Herladen, een bladwijzer en terug en vooruit in de browser werken nu zoals je verwacht:
  - het weekmenu en Recepten zoeken op `/recipes` en `/recipes/search`;
  - de aanbiedingen, de recepten erbij en de prijsgeschiedenis op `/offers`, `/offers/recipes` en `/offers/history` (met `?store=`, `?q=` en `?product=`);
  - URL importeren op `/import`;
  - de winkels op `/stores/<winkel>/<recipes|lists|orders|favorites>`, een lijstje op `/stores/ah/lists/<id>`, een bestelling op `/stores/<ah|jumbo>/orders/<nummer>` en een kassabon op `/stores/ah/receipts/<id>`;
  - de maaltijdboxen en het mandje op `/meal-kits` en `/cart`;
  - het profiel, de instellingen en Over op `/profile` (en `/profile/security`, `/profile/ai`), `/settings` (en `/settings/users`, `/settings/audit`, `/settings/system`) en `/about`.
  - `/` blijft het weekmenu. `/settings` opent alleen voor de beheerder. Staat er een winkel in het adres waarvan de koppeling na het inloggen nog laadt, dan opent dat scherm zodra de koppeling binnen is.
  - Zoeken in de prijsgeschiedenis past het adres aan zonder een stap terug per letter.
  - De server geeft op deze adressen dezelfde pagina als `/` (`auth.PAGE_ROOTS`). Ze zijn net zo openbaar als `/`, want het inlogscherm staat op de pagina zelf; `/api` blijft beschermd en een onbekend adres geeft nog steeds 404.
- **Het menu, de tabs, de winkelkeuze en het gebruikersmenu zijn echte links.** Een gewone klik blijft in de app; met Ctrl+klik (Cmd+klik op een Mac), Shift- of Alt-klik of de middelste muisknop opent de browser het scherm in een nieuw tabblad, en blijft dit tabblad staan.
- **Links tussen schermen:**
  - een aanbieding heeft de link **Prijsgeschiedenis** naar dat product;
  - in de prijsgeschiedenis linkt de bron van een aankoop (*bestelling*, *kassabon*) naar die bestelling of bon;
  - een product in een bestelling, in het mandje of op een kassabon linkt naar zijn prijsgeschiedenis;
  - de producten in de lijst van de prijsgeschiedenis, de bestellingen en de kassabonnen hebben elk hun eigen adres.
- **Oude aankopen importeren** in de **Prijsgeschiedenis** (alleen de beheerder). In je account staan alleen de bestellingen van de laatste maanden; oudere staan nog in je bevestigingsmails.
  - Met **Oude aankopen importeren** kopieer je een prompt voor een AI-assistent als Claude of ChatGPT. Geef die prompt met je bevestigingsmails aan de assistent en plak de JSON die terugkomt (ook tussen ` ```json ` en ` ``` `). Het geldt voor de winkel die bij de aanbiedingen gekozen is: AH, Picnic, Jumbo of Crisp.
  - De prompt vraagt per bestelling de datum (de bezorg- of afhaaldag) en het bestelnummer, en per product de naam, het aantal, de prijs en de korting.
  - **Controleren** laat per bestelling zien of ze nieuw is, of waarom niet: ze staat twee keer in deze JSON, is al ingelezen (zelfde bestelnummer, of zonder nummer zelfde dag en totaal), staat al in je bestellingen, of op die dag staat al een bestelling in je account. Een bestelling zonder bruikbare datum of zonder producten wordt niet ingelezen en staat bij de problemen. Pas **Importeren** leest de bestellingen in, en alleen met de tekst die je controleerde.
  - Haalt de app een ingelezen bestelling later alsnog uit je account, dan telt de ingelezen versie niet meer mee. Zo telt een aankoop nooit dubbel.
  - Een ingelezen regel heeft alleen de naam uit de mail. Die wordt op naam aan een bekend product gekoppeld, zoals een regel van een kassabon; een dubbel merk uit een mail (*AH Ah zachte spinazie*) valt weg. Past de naam nergens, dan staat de regel als eigen product in de lijst. De bron heet *import*.
  - **Ingelezen aankopen verwijderen** haalt alle ingelezen aankopen van die winkel weer weg.
  - Hooguit 5.000.000 tekens, 5000 bestellingen per keer en 1000 producten per bestelling. Een reverse proxy voor MealieShopper moet een aanvraag van ongeveer 5 MB doorlaten om de grootste import te kunnen doen.
  - `GET`, `POST` (met `dryRun` voor Controleren) en `DELETE /api/prices/import`, alleen voor de beheerder.
- **In de back-up**: de ingelezen aankopen gaan mee (*Ingelezen aankopen* in de samenvatting). De koppeling van de artikelnummers op de kassabonnen (zie hieronder) gaat niet mee: de volgende synchronisatie van de bonnen bouwt die opnieuw op.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Issue: [#178](https://github.com/helmerzNL/MealieShopper/issues/178). Pull requests: [#190](https://github.com/helmerzNL/MealieShopper/pull/190), [#192](https://github.com/helmerzNL/MealieShopper/pull/192), [#193](https://github.com/helmerzNL/MealieShopper/pull/193) en [#194](https://github.com/helmerzNL/MealieShopper/pull/194).

### Opgelost in 2.93.0

*English summary: in Price history the chart no longer runs to the year 2999: offers saved before 2.75.0 could still have AH's "ongoing" end date 31-12-2999, which is now cleared once after starting. A product on an AH receipt and the same product from an order are now one product: the id on a receipt is AH's article number (hqId), not the web shop's id, and after every receipt sync MealieShopper looks the receipt lines up at AH and only links an article number to the result with exactly that number. A receipt line no longer ends up at a completely different product whose web shop id happens to equal its article number (AH ROOKWORST at a pizza, CROKY CHIPS at chicken schnitzel): a known id now only counts when the names share at least one word, otherwise the line becomes its own product.*

- **De grafiek in de Prijsgeschiedenis loopt niet meer tot het jaar 2999.** Een aanbieding die vóór 2.75.0 werd opgeslagen, kon AH's einddatum voor *doorlopend* (`2999-12-31`) nog hebben; dan liep de grafiek tot 2999 en telde de aanbieding als nog lopend. Na het starten maakt MealieShopper die einddatum een keer leeg, in de aanbiedingen en in de aanbiedingsgeschiedenis.
- **Een product op de kassabon en hetzelfde product uit een bestelling zijn één product.** *BLOEMKOOL* op de bon en *AH Bloemkool* uit een bestelling stonden apart in de lijst. Het id op een kassabon is het artikelnummer van AH (`hqId`), niet het id van de webwinkel: *BLOEMKOOL* heeft 20960, *AH Bloemkool* 4183.
  - Na elke synchronisatie van de kassabonnen zoekt MealieShopper de naam van de bonregels bij AH op en koppelt een artikelnummer alleen aan het resultaat met precies dat artikelnummer, dus zonder te gokken op naam. Hooguit 40 artikelnummers en 20 seconden per keer, de vaakst gekochte eerst; wat AH niet vindt, probeert het na 30 dagen opnieuw. Het koppelen laat het ophalen van de bonnen nooit mislukken.
  - Is een regel niet gekoppeld, dan telt de naam, nu ook zonder *AH* ervoor: *BLOEMKOOL* hoort bij *AH Bloemkool* als maar één product zo heet.
- **Een bonregel komt niet meer bij een heel ander product terecht.** Het artikelnummer op een bon kan toevallig gelijk zijn aan het id van een ander product in de webwinkel: *AH ROOKWORST* (627748) stond zo als aankoop van € 2,99 bij *Dr. Oetker Ristorante pizza hawaii 3-pack*, en *CROKY CHIPS* bij *AH Scharrel kipschnitzel 2 stuks 2-pack*. Een bekend id telt nu alleen als de naam op de bon minstens één woord deelt met een naam van dat product; een afgekort woord telt mee (*RUNDERGEH* past bij *Rundergehakt*). Past ook de naam nergens, dan wordt de regel een eigen product.

## v2.92.0

### Nieuw in 2.92.0

*English summary: Price history has an "Only bought before" switch under the search box: with it on, the list only shows products you bought at least once (from your orders and receipts), also together with a search term; the choice is remembered in your browser. MealieShopper now also keeps the image of every offer you bought at least once, resized to at most 1024 pixels wide, with the name the product had at the time. When a store renames a product or changes its photo, the product's Saved images show whether it is still the same: a new photo is added and the old one stays (at most ten per product, the first one always stays). Images are saved while syncing the offers, also for earlier offers in the history while the store still serves them; this never makes a sync fail and takes at most 20 seconds per sync. The images are part of the backup and are read again on restore. New dependency: Pillow.*

- **Alleen eerder gekocht** in de **Prijsgeschiedenis**. Onder het zoekveld staat een schakelaar: aan, dan staan in de lijst alleen de producten die je minstens één keer kocht, volgens je bestellingen (alleen geleverde) en kassabonnen. Het zoeken werkt er gewoon bij (*gehakt* met de schakelaar aan: alleen het gehakt dat je kocht). De keuze blijft bewaard in je browser. `GET /api/prices/history` neemt `bought=1`, en de MCP-tool `get_price_history` heeft `bought`.
- **De afbeeldingen van aanbiedingen die je kocht worden bewaard**, om later te vergelijken als een product ineens anders heet:
  - Bij elke synchronisatie van de aanbiedingen haalt MealieShopper de afbeelding op van elke aanbieding waarvan je het product minstens één keer kocht, en bewaart die in de database, met de naam die het product toen had en de datum. Ook de afbeelding van een eerdere aanbieding uit de geschiedenis komt erbij, zolang de winkel die nog geeft.
  - **Verkleind tot hooguit 1024 pixels breed** (een kleinere afbeelding blijft zoals hij is, nooit vergroot; een gedraaide foto wordt rechtop gezet). Waar de winkel een grotere versie heeft (AH, Picnic en Crisp), haalt MealieShopper die op, zodat de bewaarde afbeelding zo scherp mogelijk is.
  - In de Prijsgeschiedenis staat bij het product het blok **Bewaarde afbeeldingen**: elke afbeelding met de naam van toen en *bewaard op …*; een klik opent hem in het groot. De afbeelding bovenaan het product komt nu ook uit de bewaarde kopie, dus die blijft staan als de winkel hem weghaalt.
  - Verandert de foto van een product, dan komt de nieuwe erbij en blijft de oude staan (hooguit tien per product; de eerste blijft altijd). Dezelfde foto onder een ander adres wordt niet nog eens bewaard.
  - Het ophalen laat een synchronisatie nooit mislukken en duurt per synchronisatie hooguit 20 seconden (hooguit 40 afbeeldingen); wat dan nog niet binnen is, volgt de volgende keer. Het staat in het auditlog, net als ander verkeer met de winkels.
  - `GET /api/prices/history` geeft per product `savedImages`; de afbeelding zelf staat op `GET /api/prices/images/<id>` (uit de database, nooit van de winkel).
- **In de back-up**: de bewaarde afbeeldingen gaan mee (*Bewaarde productafbeeldingen* in de samenvatting), want de winkel geeft ze niet terug als hij ze vervangt. Bij het terugzetten wordt elke afbeelding opnieuw gelezen: wat geen PNG, JPEG of WebP van hooguit 1024 pixels breed is, wordt geweigerd, en het type, de maat en de controlesom volgen uit de afbeelding zelf.
- **Nieuwe afhankelijkheid: Pillow** (voor het verkleinen). De container installeert die vanzelf; draai je MealieShopper zonder container, voer dan `pip install -r requirements.txt` opnieuw uit.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#188](https://github.com/helmerzNL/MealieShopper/pull/188).

## v2.91.2

### Opgelost in 2.91.2

*English summary: in Compare pack sizes, the count ("6 of the 8 products found"), "Searching for other packs…" and "no other packs found" were shown in a red error box; they are now plain grey text, and only a real error is red. The explanation above Find recipes now mentions Crisp, which it searches since 2.86.0. The README (Dutch and English) and its screenshots are updated to 2.91: every screenshot was made again, with new ones for the recipe window in the week menu, the price per unit and verdict on offers, Compare pack sizes, the Price history and Crisp (basket, favorites, orders and linking). Reload the page once after updating.*

- **Vergelijk verpakkingen: de telling staat niet meer in een rood foutvak.** *6 van de 8 gevonden producten*, *Andere verpakkingen zoeken…* en *Geen andere verpakkingen gevonden* stonden in een rood vak, alsof er iets mis was. Nu is het gewone grijze tekst; alleen een echte fout (bijvoorbeeld als de winkel niet antwoordt) staat nog in een rood vak.
- **De uitleg boven Recepten zoeken noemt Crisp.** Sinds 2.86.0 zoekt Recepten zoeken ook bij Crisp, maar de uitleg noemde alleen Jumbo en Picnic. Nu staat er *bij Jumbo, Picnic en Crisp zodra die gekoppeld zijn*.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

### Documentatie in 2.91.2

- **README's en screenshots bijgewerkt tot 2.91**, in het Nederlands en het Engels. Alle screenshots zijn opnieuw gemaakt met de huidige app; Crisp staat nu overal bij de gekoppelde winkels (winkelmenu, Mandje, Instellingen). Nieuw:
  - `meal-recipe`: het recept van een maaltijd in het weekmenu, met **Open in Mealie** en **Wijzigen**;
  - `compare`: **Vergelijk verpakkingen** met de filters en de kiloprijzen;
  - `price-history`: de **Prijsgeschiedenis** met de aanbiedingen, de gewone prijs als trap en je aankopen;
  - `basket-crisp`, `crisp-favorites`, `crisp-orders` en `settings-crisp`: het Crisp-mandje, de favorieten, de bestellingen en het koppelen met een code per mail.
- `offers` toont nu de prijs per eenheid en het oordeel bij elke aanbieding, en `cart` de prijs per kilo in de controlelijst met de regel *Per kg goedkoper* en **Kies**.
- De README noemt Crisp ook bij *Naar Picnic, Jumbo of Crisp*, het Mandje, de Aanbiedingen, Recepten zoeken en het winkelmenu, heeft een eigen blok *Winkels: Crisp*, en een punt *Is het echt een aanbieding?* bij *Wat kan het?*.
- Het screenshot-script kent de prijs per eenheid (`perUnit` in `scripts/screenshots/demo.js`), klapt een winkelblok onder Instellingen alleen open als het dicht is, en meldt `LET OP` als er een foutmelding in beeld staat (bijvoorbeeld omdat een nagebootst antwoord een nieuwe winkel nog niet kent).
- Pull request: [#189](https://github.com/helmerznl/MealieShopper/pull/189).

## v2.91.1

### Opgelost in 2.91.1

*English summary: with a popup open (the recipe of a meal, Edit meal, a product photo, Compare packs or an offer), swiping on a phone sometimes scrolled the page behind it: as soon as the popup reached its top or bottom, on a short popup, or on the dark edge around it. Now only the popup scrolls; the page behind it stays put and keeps its place, swiping down no longer reloads the page (pull-to-refresh), and the mouse wheel behaves the same. After closing, the page scrolls as before.*

- **Vegen in een popup scrolt alleen de popup.** Met een popup open (het recept van een maaltijd, *Maaltijd wijzigen*, de foto van een product, *Verpakkingen vergelijken* of een aanbieding) scrolde de pagina erachter soms mee: zodra de popup bovenaan of onderaan was, bij een korte popup, en bij vegen op de donkere rand eromheen. Nu staat de pagina erachter stil zolang er een popup open is, en houdt ze haar plek. Vegen naar beneden ververst de pagina ook niet meer (pull-to-refresh). Met het muiswiel werkt het net zo. Na het sluiten scrolt de pagina weer gewoon.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#186](https://github.com/helmerzNL/MealieShopper/pull/186).

## v2.91.0

### Nieuw in 2.91.0

*English summary: in the week menu, tapping a meal now opens its recipe in a window instead of the edit screen: the ingredients (already scaled to the servings you planned), the method, the cooking time and the description, with Open in Mealie, Edit and Close. The pencil at the top right of a meal is now its own button and opens Edit meal, as does Edit in the recipe window. A note has no recipe, so tapping it still opens Edit meal. The window needs no extra request: the week already brings each recipe from Mealie.*

- **Een tik op een maaltijd in het weekmenu opent het recept**, in een venster: de foto, de maaltijd en de dag, de porties en de bereidingstijd, de omschrijving, de ingrediënten (al omgerekend naar de porties die je in Mealie plande, net als *Alle ingrediënten*) en de bereiding. Met **Open in Mealie**, **Wijzigen** en **Sluiten**; Esc of een tik naast het venster sluit het ook. De knoppen blijven onderaan in beeld terwijl je door een lang recept scrolt.
- **Het potlood rechtsboven bij een maaltijd is een eigen knop** en opent *Maaltijd wijzigen* (de dag, de maaltijd, het recept, of Verwijderen). Vroeger opende een tik op de hele maaltijd dat venster. **Wijzigen** in het receptvenster opent hetzelfde.
- Een notitie (zoals *Uit eten*) heeft geen recept: een tik daarop opent nog steeds meteen *Maaltijd wijzigen*.
- Het venster vraagt niets nieuws aan Mealie: de week haalt elk recept al op. `GET /api/mealplan` geeft per maaltijd ook `recipeMinutes` (de bereidingstijd) en `recipeUrl` (het recept in Mealie).
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.
- Pull request: [#184](https://github.com/helmerzNL/MealieShopper/pull/184).

## v2.90.0

### Nieuw in 2.90.0

*English summary: the pack comparison now also works in the check list when you put the week menu in your basket. Every product in a line's drop-down shows its price per kilo, litre or piece (with an offer: what an item costs with the deal, also for "1+1 free"). After the suggested product the drop-down only lists the same product in another size or what is cheaper per unit, and only the same kind of product (for minced beef no mixed mince or meatballs); other search results are left out and remain available through Search for another product. When another option is cheaper per unit, a line under the ingredient says so ("Cheaper per kg: AH Rundergehakt 500 g – €10.70/kg (chosen: €12.63/kg)") with a Choose button, which also recalculates the number of packs. Searching yourself still shows the first five results, now with the price per unit.*

- **Vergelijken in de controlelijst van het weekmenu** ([#178](https://github.com/helmerzNL/MealieShopper/issues/178), op verzoek). Als je het weekmenu in je mandje doet:
  - staat bij elk product in de keuzelijst de **prijs per kilo, liter of stuk**. Bij een aanbieding is dat wat een stuk met de actie kost, ook bij *1+1 gratis* (waar AH de gewone prijs als prijs geeft);
  - staat na het voorgestelde product alleen nog wat **hetzelfde product** is in een andere maat, of **per eenheid goedkoper**, en alleen hetzelfde soort product: bij rundergehakt dus 500 g en 175 g rundergehakt en *minder vet*, maar geen gemengd gehakt, gehaktballen, biologisch van 250 g of Greenfields. De goedkoopste per eenheid eerst, hooguit zes. Andere zoekresultaten vind je met **Zoek een ander product**;
  - staat onder het ingrediënt een regel als een andere optie per eenheid goedkoper is, bijvoorbeeld *"Per kg goedkoper: AH Rundergehakt 500 g – € 10,70/kg (gekozen: € 12,63/kg)"*, met **Kies**. Dat kiest die optie en rekent het aantal verpakkingen opnieuw uit voor de hoeveelheid uit het recept (voor 500 g: één keer 500 g in plaats van twee keer 300 g).
- Bij AH vroeg de zoekopdracht al om 30 producten en hield er vijf over; die 30 worden nu gebruikt, dus er zijn geen extra aanroepen. Bij Picnic, Jumbo en Crisp vraagt dezelfde zoekopdracht om meer producten.
- **Zoek een ander product** laat de eerste vijf zoekresultaten zien zoals voorheen, nu ook met de prijs per eenheid.
- Pull request: [#183](https://github.com/helmerzNL/MealieShopper/pull/183).

## v2.89.2

### Nieuw in 2.89.2

*English summary: Compare pack sizes can now be filtered. By default it shows only the same kind of product (every word of the product name without brand, sizes and pack words must be in the name: "AH Rundergehakt" shows 300 g, 1000 g and organic mince, but not burgers or meatballs); "Same brand", "On offer" and a name filter narrow it further, and the selected product always stays for comparison. At the top it says which visible product is cheapest per unit and by how much, or that this product is the cheapest. The search asks the store for 30 products instead of 10, so the big pack is among them. Fixed: AH gives "1+1 free", "2nd half price" and "2nd = 50% off" with the regular price as the price and no price before, so the per-item price, the verdict and the comparison used the full price (300 g mince on 1+1 showed €11.63/kg instead of €5.83/kg); the per-item price now follows from that regular price, and the offer card shows it too.*

- **Vergelijk verpakkingen is te filteren** ([#178](https://github.com/helmerzNL/MealieShopper/issues/178), op verzoek):
  - **Zelfde soort** (standaard aan): alleen producten waarin elk woord van de productnaam staat, zonder merk, winkel, maten en woorden als *voordeelverpakking*. Bij *AH Rundergehakt* zie je 300 g, 1000 g, biologisch en mager rundergehakt, maar geen hamburgers, gehaktballen of half-om-half.
  - **Zelfde merk**, **In de aanbieding** en een veld **Filter op naam**. De filters werken op wat er al gevonden is, zonder nieuwe zoekopdracht.
  - Het gekozen product blijft altijd staan, om mee te vergelijken. De kleur volgt de plek in wat er te zien is.
  - **Bovenaan het antwoord**: *"AH Rundergehakt 1000 g is per kg € … goedkoper dan dit product"*, of *"Dit product is per kg het goedkoopst van deze 4"*. Daaronder hoeveel van de gevonden producten je ziet.
  - MealieShopper vraagt de winkel nu om 30 producten in plaats van 10, zodat ook de grote verpakking ertussen zit.
- Pull request: [#182](https://github.com/helmerzNL/MealieShopper/pull/182).

### Opgelost in 2.89.2

- **Prijs per stuk bij "1+1 gratis" en "2e halve prijs" van AH.** AH geeft bij die acties de gewone prijs als prijs mee, zonder prijs ervoor. Daardoor rekende MealieShopper met de volle prijs: 300 g rundergehakt met 1+1 gratis stond op € 11,63/kg in plaats van € 5,83/kg, met een verkeerd oordeel en een verkeerde vergelijking. Nu volgt de prijs per stuk uit die gewone prijs, voor *1+1 gratis* (en *2+1*), *2e halve prijs* en *2e = 50% korting*. De kaart van de aanbieding toont hem ook (*bij 2 stuks · € 1,75 per stuk*). Bij *25% korting* zonder prijs ervoor rekent MealieShopper niets uit, omdat niet zeker is of de prijs al verlaagd is.

## v2.89.1

### Opgelost in 2.89.1

*English summary: fetching the AH Bonus failed with "AH bonus ophalen mislukt (400): member not active / invalid_grant". The Bonus, product search and recipes use an anonymous AH token, which AH says is valid for seven days but sometimes revokes earlier; MealieShopper kept using the revoked token until it expired, so every request failed for up to a week. MealieShopper now keeps the anonymous token for at most an hour, and when AH rejects a token it forgets it and tries once more with a fresh one. A fresh token was checked live against AH and works. Also fixed: searching the Price history for "Gehakt 500 gram" found nothing, because the whole text had to be in the product name; now every word must be in the name (also as part of a word) and a size in the search is compared with the pack.*

- **De AH Bonus ophalen mislukte met "AH bonus ophalen mislukt (400): member not active / invalid_grant".** De bonus, het zoeken naar producten (ook in de controlelijst van het weekmenu) en de recepten gebruiken een anoniem AH-token, zonder je account. AH zegt dat zo'n token zeven dagen geldig is, maar trekt het soms eerder in. MealieShopper bleef het ingetrokken token gebruiken tot het zou verlopen, dus elke aanroep mislukte tot een week lang, ook de terugvalroutes van de bonus.
  - MealieShopper bewaart het anonieme token nu hooguit een uur.
  - Weigert AH een token (401, of 400/403 met `invalid_grant`, "member not active" of `invalid_token`), dan vergeet de app het en probeert de aanroep één keer opnieuw met een vers token. Dat geldt voor de bonus, het zoeken naar producten, producten op id, recepten zoeken en een recept ophalen.
  - Een geweigerd token van je AH-koppeling wordt ook vergeten, zodat de volgende aanroep het opnieuw ververst.
- Live nagekeken: een vers anoniem token haalt de bonus gewoon op.
- Pull request: [#181](https://github.com/helmerzNL/MealieShopper/pull/181).
- **Zoeken in de Prijsgeschiedenis op woorden en maat.** "Gehakt 500 gram" vond niets: het zoeken keek of die hele tekst letterlijk in de productnaam stond, en de maat staat apart ("500 g"). Nu moet elk woord in de naam (of het merk) staan, ook als deel van een woord ("gehakt" vindt "AH Rundergehakt"), in elke volgorde. Een maat in de zoekterm ("500 gram", "500g", "0,5 kg", "2 x 250 g") wordt vergeleken met de verpakking: gehakt van 500 gram wel, dat van 300 gram niet. Een product waarvan de verpakking niet bekend is, zoals een regel van een kassabon zonder maat, blijft erin. Geldt ook voor de MCP-tool `get_price_history`.

## v2.89.0

### Nieuw in 2.89.0

*English summary: part 2 of the price history (#178). Every offer now shows its price per kilo, litre or piece and a verdict: "A real deal" (what an item costs with the offer is at least 20% below the lowest regular price of the last 30 days), "Small saving", or "Not cheaper than the regular price of the last 30 days", with a note when the store's price before is higher than that. Offers also say when this is the lowest offer price in a year, when the product is on offer six times a year or more, and when another pack of the same product is cheaper per unit. The new "Compare pack sizes" button (on an offer and in Price history) searches the store once and lists the packs with the same unit, cheapest per unit first, coloured green to red. Ideas and test cases come from wolph's userscript. Fixed: purchases in the store now all count in the price history. A line on an AH receipt without a usable product id was left out, and an unknown id became a separate product; now a line is linked by its id when that is known, otherwise by its name ("AH RUNDERGEHAKT" belongs to "AH Rundergehakt"), and otherwise it is listed as its own product. The history also says how many orders and receipts still have to be fetched.*

- **Prijs per kilo, liter of stuk bij elke aanbieding**, uit de verpakking: *2 x 250 g* is 500 gram, *6 x 1 l* zes liter (de prijs van het pak gedeeld door zes, niet de literprijs zelf), *ca. 500 g* 500 gram en *per stuk* één stuk. Bij een aanbieding met een voorwaarde (*2 voor 3*, *2e halve prijs*, *1+1 gratis*) is het wat een stuk met de actie kost.
- **Een oordeel bij elke aanbieding:**
  - *Echt voordelig*: wat een stuk met de actie kost, ligt minstens 20% onder de laagste gewone prijs van de laatste 30 dagen.
  - *Klein voordeel*: lager, maar minder dan 20%.
  - *Niet goedkoper dan de gewone prijs van de laatste 30 dagen*: je kon het onlangs voor die prijs of minder kopen.
  - Noemt de winkel een prijs ervoor die hoger is dan de laagste gewone prijs van de laatste 30 dagen, dan staat dat in de uitleg (houd de muis op het oordeel).
  - Daarnaast *Laagste actieprijs in een jaar*, *n× in de aanbieding dit jaar: komt vaak terug* (vanaf zes keer) en *Per eenheid goedkoper*: een andere verpakking van hetzelfde product (dezelfde naam zonder maten en woorden als *voordeelverpakking* of *3-pack*, en hetzelfde merk) die per eenheid minstens 2% goedkoper is, uit de bewaarde prijzen en de andere aanbiedingen.
  - Het oordeel komt uit de gegevens van deel 1 (de bewaarde gewone prijzen, je bestellingen en de aanbiedingsgeschiedenis); er gaat niets naar een winkel of een AI. Een nieuw bewaarde prijs ververst de lijst (ETag).
- **Vergelijk verpakkingen** (knop bij een aanbieding en in de Prijsgeschiedenis): zoekt één keer bij de winkel naar hetzelfde product en zet de gevonden verpakkingen met dezelfde eenheid naast elkaar, de goedkoopste per eenheid bovenaan, gekleurd van groen naar rood. *Dit product*, *zelfde product*, *goedkoper per eenheid* en *in de aanbieding* staan erbij. De gevonden prijzen worden bewaard, zodat het oordeel daarna beter wordt. Nieuwe route: `GET /api/prices/compare` (met `store` en `id`).
- **De kaart van een aanbieding** heeft nu de titel als link naar de winkel (in plaats van de hele kaart), zodat er een knop op kan.
- **Prijsgeschiedenis:** de gewone prijs per eenheid bij de kerngetallen; de MCP-tool `get_price_history` geeft hem ook (`regularPricePerUnit`).
- Ideeën en testgevallen voor de prijs per eenheid (het kortingspercentage, kleuren naar plek, *6 x 1 l* is de prijs van het pak) komen uit het userscript *Albert Heijn kortingspercentages en prijs per kg/ml/etc.* van [wolph](https://github.com/wolph) (BSD-3-Clause); de code is eigen werk.
- Issue: [#178](https://github.com/helmerzNL/MealieShopper/issues/178) (deel 2). Pull request: [#180](https://github.com/helmerzNL/MealieShopper/pull/180).

### Opgelost in 2.89.0

- **Prijsgeschiedenis: aankopen in de winkel tellen weer allemaal mee.** Rundergehakt dat je tien keer kocht, kwam er maar een paar keer in terug. Een regel op een AH-kassabon zonder bruikbaar product-id (leeg of 0) liet de geschiedenis weg, en een id dat bij geen bekend product hoorde werd een apart product. Nu hoort een regel bij het product met hetzelfde id als dat bekend is. Anders telt de naam: "AH RUNDERGEHAKT" hoort bij "AH Rundergehakt". Past ook de naam nergens, dan staat de regel als eigen product in de lijst, met de naam van de bon, zodat de aankoop altijd te vinden is.
- **Nog niet opgehaalde aankopen staan erbij.** Een synchronisatie haalt de producten van een deel van je bestellingen en kassabonnen per keer op. Boven de lijst staat nu van hoeveel bestellingen en kassabonnen dat nog moet gebeuren; die aankopen ontbreken tot je ze synchroniseert onder Winkels.

## v2.88.0

### Nieuw in 2.88.0

*English summary: the Price history (Offers, Price history) now shows more than the offers. It also shows the regular price over time and what you paid. The regular price is kept whenever MealieShopper looks a product up at the store (the check list of the week menu, Search for another product, the search per store and the MCP search tools), and it also comes from the price before an offer and from your orders. What you paid comes from the orders and receipts the app already keeps (AH, Jumbo, Picnic and Crisp), per item, so the history fills itself from everything the app ever fetched. The chart draws the regular price as steps and every purchase as a dot, with a legend and a table of purchases; products you only bought or looked up are found too. New: `GET /api/prices/history` and the MCP tool `get_price_history`. The new table `price_observations` is in the backup.*

- **Prijsgeschiedenis: ook de gewone prijs en wat je betaalde.** Onder **Aanbiedingen, Prijsgeschiedenis** stond alleen wanneer een product in de aanbieding was. Nu staan er ook bij:
  - **De gewone prijs door de tijd.** MealieShopper bewaart die elke keer dat het een product bij de winkel opzoekt: de controlelijst van het weekmenu, *Zoek een ander product*, het zoeken per winkel en de zoektools van de MCP-server. Eén prijs per product per dag. Daarnaast tellen de prijs vóór de actie van een aanbieding en de normale prijs in je bestellingen mee. Een product in de aanbieding zonder bekende prijs ervoor slaat het over, zodat er nooit een actieprijs als gewone prijs in komt.
  - **Wat je betaalde**, per stuk, uit de bestellingen en kassabonnen die de app al bewaart: AH (geleverde bestellingen en kassabonnen), Jumbo, Picnic en Crisp. Een bestelling die nog niet geleverd is, telt niet mee, een geannuleerde ook niet. Zo staat alles wat de app ooit ophaalde er meteen in, zonder aparte import. Op een kassabon staat de prijs zonder de korting onderaan de bon; dat staat er ook bij.
- **De grafiek** toont de gewone prijs als een trap (een trede loopt door tot de volgende keer dat een andere prijs is gezien) en elke aankoop als een stip, naast de balken van de aanbiedingen. Eronder staan een legenda en een tabel met de aankopen. Bovenaan staan ook hoe vaak je het kocht, wat je de laatste keer en gemiddeld per stuk betaalde, en de laagste en hoogste gewone prijs. Bij een grafiek van meer dan een jaar staat het jaartal bij de datums.
- **Ook producten die nooit in de aanbieding waren**, maar die je kocht of opzocht, zijn nu te vinden. Een naam van een kassabon vindt het product ook ("RIJST BASMATI"), en dan staan alle gegevens van dat product erbij.
- **`GET /api/prices/history`** (met `store`, `q` en `limit`) geeft per product de aanbiedingen (zoals `GET /api/offers/history`), de periodes met dezelfde gewone prijs (`regular`), de aankopen (`purchases`) en samenvattingen (`regularPrice`, `lastPaid`, `averagePaid`, `timesBought`). Het tabblad gebruikt deze route; `GET /api/offers/history` blijft bestaan.
- **MCP-tool `get_price_history`**: dezelfde gegevens, compact, zodat een assistent kan zeggen of een aanbieding echt voordelig is.
- **Back-up:** de nieuwe tabel `price_observations` gaat mee in de back-up ("Bijgehouden prijzen").
- Issue: [#178](https://github.com/helmerzNL/MealieShopper/issues/178) (van [wolph](https://github.com/wolph)). Pull request: [#179](https://github.com/helmerzNL/MealieShopper/pull/179). Dit is deel 1; deel 2 (prijs per kilo of liter, een oordeel bij elke aanbieding en verpakkingen vergelijken) volgt.

## v2.87.1

### Opgelost in 2.87.1

*English summary: the week menu sometimes picked a product that has nothing to do with the ingredient at Albert Heijn. For "AH Penne" it suggested "AH Guacamole seasoning mix 3-pack": AH's own search put that multipack (which AH files under Penne) first and AH Penne itself ninth, while MealieShopper only asked AH for the first five. MealieShopper now asks AH for 30 products and puts a product named exactly like the ingredient first (with or without AH's own brand in front: "penne" is AH Penne, but "Pringles Paprika" is not paprika), and a single product before a multipack; otherwise AH's order stays. This applies to the check list, Search for another product, `POST /api/ah/cart` for a line without a product and the MCP tool `search_products`.*

- **Het weekmenu vindt bij Albert Heijn het juiste product.** Bij "AH Penne" stelde de controlelijst "AH Guacamole seasoning mix 3-pack" voor. Dat kwam van AH zelf: de zoekfunctie van AH zette die multipack (door AH bij Penne ingedeeld) bovenaan en AH Penne pas op plek 9, en MealieShopper vroeg AH alleen om de eerste vijf. Nu vraagt MealieShopper AH om 30 producten en zet het ze zelf op volgorde:
  - Een product dat precies zo heet als het ingrediënt komt eerst, ook met AH's eigen merk ervoor: "penne" en "AH Penne" geven AH Penne, "spaghetti" geeft AH Biologisch Spaghetti. Een ander merk telt niet mee: "Pringles Paprika" is geen paprika.
  - Een los product komt vóór een multipack van AH ("AH Zalmfilet 3-pack"). Voor een recept wil je er één; het aantal verpakkingen rekent MealieShopper zelf uit.
  - Verder blijft de volgorde van AH. Of het ingrediënt letterlijk in de productnaam staat, telt bewust niet: kaas van Milner heet "Jong belegen 35+ plakken".
- Geldt voor de controlelijst van het weekmenu, *Zoek een ander product*, `POST /api/ah/cart` voor een regel zonder product en de MCP-tool `search_products` (bij Albert Heijn).
- Issue: [#175](https://github.com/helmerzNL/MealieShopper/issues/175). Pull request: [#176](https://github.com/helmerzNL/MealieShopper/pull/176).

## v2.87.0

### Nieuw in 2.87.0

*English summary: Crisp gets the same treatment as Picnic. In the Basket tab you can change the Crisp basket: quantities with `−` and `+` or by typing, remove a product, or empty the basket after a confirmation; below it are what you pay and how much more you need to reach Crisp's minimum order. Under Stores, Crisp has Favorites (the products you saved in the Crisp app, each with an "In basket" button) and Orders: upcoming orders live, delivered orders in a local copy you update with Sync, with "Add all to basket" per order. The address, IBAN and payment details of an order are never stored. Crisp's offers of this week are in the Offers tab and in the daily sync; Crisp gives no validity period in its promo overview, so they are shown in the current week, like Picnic's. As before, the routes come from the source code in the public Crisp app; Explore Crisp routes now also checks orders, favorites and offers.*

- **Het Crisp-mandje aanpassen** (tabblad Mandje). Net als bij Picnic: aantallen met `−` en `+` of door te typen, een product weghalen, of het mandje legen na een bevestiging. Het gaat direct naar je echte Crisp-mandje, in één verzoek per wijziging (`PUT /basket/main` met het nieuwe aantal; 0 haalt het product weg, zoals in de app). Legen haalt alle losse producten weg; recepten die je in de Crisp-app in het mandje zette, blijven staan. Eronder staan het totaal, wat je betaalt (met bezorg- en servicekosten) en hoeveel er nog bij moet tot het minimumbedrag. Weigert Crisp een wijziging, dan zegt MealieShopper dat in eigen woorden en blijft het mandje zoals het was. Er wordt nooit een bestelling geplaatst.
- **Crisp onder Winkels: Favorieten en Bestellingen.**
  - **Favorieten** zijn de producten die je in de Crisp-app hebt bewaard ("Door jou bewaard"), met een zoekveld en per product een knop **In mandje**.
  - **Bestellingen** toont je aankomende bestellingen live en je bezorgde bestellingen uit een lokale kopie (`crisp_orders`, ook in de back-up). **Synchroniseren** bladert door je bestelgeschiedenis bij Crisp tot een pagina die al bekend is en haalt per keer de producten van hooguit 25 bestellingen op. Open een bestelling voor de producten en zet ze met **Alles in mandje** opnieuw in je Crisp-mandje.
  - Het adres, het IBAN, de betaalwijze, de factuur en de track-and-trace van een bestelling bewaart en toont MealieShopper niet.
- **Crisp-aanbiedingen** in het tabblad Aanbiedingen, ook in de dagelijkse synchronisatie en in **Aanbiedingen synchroniseren**. MealieShopper leest de promopagina van de app: losse producten en groepen (met actieprijs, gewone prijs en actietekst) en acties met hun producten (de titel van de actie is dan het label). Crisp geeft daar geen looptijd bij; de aanbiedingen staan daarom bij de huidige week, zoals die van Picnic.
- **Crisp-routes verkennen** controleert ook de bestelgeschiedenis, één bestelling, de open bestellingen, de favorieten en de promopagina, met alleen soorten, aantallen en veldnamen (geen id's, adressen of prijzen).
- **API**: `PUT /api/crisp/cart/items/<id>`, `POST /api/crisp/cart/clear` (met `{"confirm": true}`), `GET /api/crisp/orders`, `GET /api/crisp/orders/open`, `GET /api/crisp/orders/<id>`, `POST /api/crisp/orders/sync` en `GET /api/crisp/favorites`. `crisp` is een geldige winkel voor `/api/offers`.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#174](https://github.com/helmerzNL/MealieShopper/pull/174).

## v2.86.1

### Opgelost in 2.86.1

*English summary: Crisp recipes and products now show their photos. Crisp gives a photo as a blob id, not as an address; MealieShopper now builds the address the way the Crisp app does (`https://crispapp.nl/blob/<id>?thumb=<size>`), in Find recipes, in the recipe view and in the check list.*

- **Foto's bij Crisp-recepten en -producten.** Bij Recepten zoeken, in het recept en in de controlelijst ontbrak de foto. Crisp geeft een foto als blob-id, niet als adres; MealieShopper liet die weg. Nu maakt het het adres zoals de Crisp-app (`https://crispapp.nl/blob/<id>?thumb=<maat>`): 512 voor de kaarten, 1024 voor het recept en de foto in het groot.

- Pull request: [#173](https://github.com/helmerzNL/MealieShopper/pull/173).

## v2.86.0

### Nieuw in 2.86.0

*English summary: Crisp, the Dutch app-only online supermarket, is now a store in MealieShopper. The administrator links it under Settings, Link, Crisp with the email address of the Crisp account and a code that Crisp emails (the Netherlands or Belgium; no password, only the device id is stored, encrypted). The week menu then goes into the real Crisp basket through the same check list as the other stores, Find recipes also searches the recipes in the Crisp app (view them, import them into Mealie or plan them), and the Basket tab shows the Crisp basket read-only. Crisp has no official API: logging in follows the open-source home-assistant-crisp project, search, recipes and the basket follow the public Crisp app. Explore Crisp routes shows which routes work.*

- **Crisp als supermarkt.** Crisp heeft geen webshop en geen officiële API; MealieShopper praat met de app-API zoals de Crisp-app zelf. Inloggen en de headers volgen het open-source project [home-assistant-crisp](https://github.com/NLthijs48/home-assistant-crisp) (MIT); de routes voor zoeken, recepten en het mandje komen uit de broncode in de openbare Crisp-app. Het onderzoek, ook naar de voorwaarden van Crisp naast die van Picnic, Jumbo en Albert Heijn, staat in [docs/onderzoek-crisp.md](docs/onderzoek-crisp.md).
- **Koppelen met een code per mail** (Instellingen, Koppelen, Crisp; alleen de beheerder). Kies Nederland of België, vul het e-mailadres van je Crisp-account in en daarna de code uit de mail. Er is geen wachtwoord: MealieShopper maakt een apparaat-id zoals de app, Crisp koppelt je account daaraan en MealieShopper bewaart alleen dat id, versleuteld (en in de back-up bij de koppelingen). `Ontkoppelen` meldt het apparaat bij Crisp af en wist het. `Koppeling controleren` vraagt Crisp of het apparaat nog is ingelogd.
- **Het weekmenu naar het Crisp-mandje.** Crisp staat in de winkelkeuze van de planner zodra het gekoppeld is. Dezelfde controlelijst als bij de andere winkels: per ingrediënt de producten van Crisp, het aantal verpakkingen uit het gewicht dat Crisp noemt, en aanbiedingen (een lagere prijs dan de gewone prijs of een actie). Na je bevestiging gaan ze in één keer in je echte Crisp-mandje; een product dat Crisp niet kan leveren, wordt gemeld. Er wordt nooit een bestelling geplaatst.
- **Crisp-recepten zoeken, bekijken en in Mealie zetten.** `Recepten zoeken` zoekt ook in de recepten van de Crisp-app (met de zoekfunctie van Crisp), met `Bekijken` (ingrediënten, ook wat je zelf toevoegt, en de bereiding), `Importeer` en `Inplannen`. Een recept dat al uit Crisp in Mealie staat, wordt bij het inplannen hergebruikt.
- **Het Crisp-mandje in het tabblad Mandje**, alleen om te bekijken: producten, aantallen, gewicht en prijs. Aanpassen, een bezorgmoment kiezen en bestellen doe je in de Crisp-app.
- **Crisp-routes verkennen** (alleen de beheerder) roept de routes voor gebruiker, zoeken, product, mandje en recepten aan en toont welke werken en welke velden erin zitten, zonder namen, adressen, prijzen of id's.
- **Auditlog**: het verkeer met `crispapp.nl` staat onder de dienst Crisp. De installatiehulp kent Crisp nog niet; koppel het onder Instellingen.
- **API**: `/api/crisp/auth` (code aanvragen), `/api/crisp/auth/verify`, `/api/crisp/auth/logout`, `/api/crisp/auth/status`, `/api/crisp/search`, `/api/crisp/cart/preview`, `/api/crisp/cart` (lezen en vullen), `/api/crisp/recipes`, `/api/crisp/recipes/<id>`, `/api/crisp/recipes/<id>/import` en `/api/crisp/explore`.

- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#170](https://github.com/helmerzNL/MealieShopper/pull/170).

## v2.85.2

### Opgelost in 2.85.2

*English summary: the explanation above the recipes under Stores > Picnic and Stores > Jumbo still said "open a recipe, or import it into Mealie". Since 2.83.0 every card has **View** and **Plan**, and importing without planning only happens from the recipe itself; the text now says so, like Find recipes and Meal kits. Reload the page once after updating.*

- **De uitleg boven de recepten bij Winkels > Picnic en Winkels > Jumbo klopt weer.** Er stond nog *Open een recept voor de ingrediënten en de bereiding, of importeer het in Mealie*. Sinds 2.83.0 ([#162](https://github.com/helmerzNL/MealieShopper/pull/162)) heeft elke kaart **Bekijken** en **Inplannen**, en importeren zonder in te plannen doe je alleen in het recept zelf. Er staat nu, net als bij Recepten zoeken en Maaltijdboxen: *Bekijk een recept voor de ingrediënten en de bereiding, of plan het in je weekmenu; dan komt het ook in Mealie.* De README (Winkels: Picnic) is op dezelfde manier bijgewerkt, in het Nederlands en het Engels, en de screenshots `picnic-recipes` en `jumbo-recipes` zijn opnieuw gemaakt.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#172](https://github.com/helmerzNL/MealieShopper/pull/172).

## v2.85.1

### Opgelost in 2.85.1

*English summary: Find recipes has more breathing room: the explanation no longer sticks to the search field, each store (Mealie, Albert Heijn, Jumbo and so on) starts with space above its heading, and **More recipes** no longer touches the cards above it. A stable release now carries the release notes of every version since the previous stable release, so features that only came out as a beta are in it too. Reload the page once after updating.*

- **Meer ruimte in Recepten zoeken.** De uitleg onder het zoekvak stond er direct tegenaan; er zit nu 10 px tussen. Elke winkel in de resultaten (Mealie, Albert Heijn, Jumbo, Picnic, HelloFresh, Marley Spoon) begint met 24 px ruimte boven het kopje, zodat het kopje niet meer aan de uitleg of aan de kaarten van de winkel erboven plakt. **Meer recepten** staat 14 px onder de kaarten in plaats van ertegenaan.
- **De stabiele release bevat alle versies sinds de vorige stabiele release.** Bij de promotie van `beta` naar `main` kreeg de stabiele release alleen de tekst van de laatste versie; wat ertussen alleen als beta uitkwam, stond er niet in. `release.yml` zoekt nu de vorige stabiele release op en `scripts/release_notes.py --since` zet de teksten van alle versies daarna onder elkaar, nieuwste eerst. Een pre-release krijgt nog steeds alleen zijn eigen versie. De sectie van 2.84.0 is daarbij opgeschoond: de porties (#164) en de productfoto's (#163) staan er nu samen in.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull requests: [#167](https://github.com/helmerzNL/MealieShopper/pull/167) en [#168](https://github.com/helmerzNL/MealieShopper/pull/168).

## v2.85.0

### Nieuw in 2.85.0

*English summary: the Offers tab now shows the offers of the week you are planning ([#160](https://github.com/helmerzNL/MealieShopper/issues/160)). The week is at the top with the same arrows as the week menu, and both share one week: plan next week and you see next week's offers, pick a week in Offers and the week menu follows. The AH Bonus now comes per Bonus period from the GraphQL of the AH app: AH's Bonus week runs Monday to Sunday like the week menu, and next week's complete Bonus is there as soon as AH gives it at the weekend (about 3,000 products a week, where searching found a few hundred and only some 80 of next week). A product on Bonus in both weeks keeps both deals. The recipe suggestions use the same week and are about 40 times faster. The header names the Bonus week again instead of "Valid Sun 1 Nov to Sun 1 Nov".*

- **Aanbiedingen per week, gelijk met het weekmenu** ([#160](https://github.com/helmerzNL/MealieShopper/issues/160)). Bovenaan het tabblad Aanbiedingen (en bij Recepten) staat de week, met dezelfde pijltjes als in het weekmenu. Het weekmenu en Aanbiedingen delen de week: plan je volgende week, dan zie je de aanbiedingen van volgende week, en kies je bij Aanbiedingen een andere week, dan gaat het weekmenu mee. Eerdere weken zijn er niet; verlopen aanbiedingen staan in de Prijsgeschiedenis.
- **De AH Bonus per bonusperiode, compleet, ook van volgende week.** MealieShopper haalt de bonus nu uit de GraphQL van de AH-app (`bonusPeriods` en `bonusPromotions`): AH's bonusweek loopt van maandag t/m zondag, net als het weekmenu, en de bonus van volgende week staat erin zodra AH die in het weekend doorgeeft. Eén aanvraag van een paar seconden geeft per actie de producten, hun normale prijs en de actietekst (*2e halve prijs*, *2 voor 2.99*, *voor 1.99*). Dat zijn zo'n 3.000 producten per week; het zoeken met de bonusvlag vond er een paar honderd, en van volgende week zo'n 80. De landelijke en de online bonus doen mee, bezorgkorting en de acties van Etos en Gall niet. Lukt de aanvraag niet, dan valt MealieShopper terug op de bonuspagina en het zoeken.
- **Een product kan in twee weken een eigen actie hebben.** Is gehakt deze week 25% korting en volgende week *2 voor 5*, dan staat het bij elke week met die actie. De tabel `offers` heeft daarvoor geen sleutel meer op winkel en product; een bestaande tabel wordt bij de eerste start een keer omgebouwd, met dezelfde rijen. Een back-up van een eerdere versie is gewoon terug te zetten.
- **Bij deze week staat wat er al voor later bekend is**: *Er zijn al … aanbiedingen bekend die later beginnen*, met **Bekijk de week erna**.
- **Een aanbieding hoort bij elke week waarin ze geldt.** Een doorlopende korting zonder einddatum (zoals AH's *volume voordeel*) staat in elke week, na de aanbiedingen met een einddatum, zodat de bonus van de week bovenaan staat. Staat een product twee keer in dezelfde week, dan wint de meest specifieke actie (een weekactie gaat voor een actie die tot de feestdagen loopt). Een aanbieding zonder looptijd (Picnic geeft er geen) staat alleen bij de huidige week; bij een andere week staat erbij hoeveel dat er zijn. Begint een aanbieding nog en pas later in de gekozen week (Jumbo wisselt op woensdag), dan staat er *vanaf …* bij.
- **De receptvoorstellen gebruiken de aanbiedingen van dezelfde week, en zijn zo'n 40 keer sneller.** Er werd elk ingrediënt met elke aanbieding vergeleken; nu alleen met de aanbiedingen die het hoofdwoord in hun naam kunnen hebben, met precies dezelfde uitkomst. 400 recepten tegen de hele bonus van een week kost ongeveer een seconde. *Recepten zoeken met AI* krijgt het aanbod van deze week.
- **De AH-verkenner** toont per bonusperiode hoeveel acties en producten AH geeft, zodat te zien is of de bonus van volgende week er al staat.
- **API**: `GET /api/offers` en `GET /api/offers/recipes` nemen `week` (een datum in de week, zoals `2026-11-02`); zonder `week` is het de week van vandaag. Het antwoord meldt `week`, `later` (aanbiedingen die pas na die week beginnen) en `undated` (aanbiedingen zonder looptijd die voor een andere week zijn weggelaten). Elke week heeft een eigen `ETag`.

### Opgelost in 2.85.0

- **"Geldig zo 1 nov t/m zo 1 nov".** De regel boven de aanbiedingen noemt de looptijd die de meeste aanbiedingen delen. AH geeft zijn pakketkortingen en volumevoordelen elke dag opnieuw door met een looptijd van alleen die dag, en dat waren er meer dan de bonus van de week; op zondag stond er daardoor alleen vandaag. Een looptijd van één dag telt nu alleen als er geen langere is, zodat er weer *Geldig ma 26 okt t/m zo 1 nov* staat.
- **De bonus van volgende week stond tussen die van vandaag**, en kwam al in de prijsgeschiedenis voordat hij begon. De geschiedenis schrijft een aanbieding nu pas als ze begint.
- **"voor 1.99"** (AH's schildje voor een vaste actieprijs) wordt gelezen als een vaste prijs.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#166](https://github.com/helmerzNL/MealieShopper/pull/166); issue: [#160](https://github.com/helmerzNL/MealieShopper/issues/160).

## v2.84.1

### Opgelost in 2.84.1

*English summary: in a recipe opened from Find recipes (and from the stores and meal kits), the photo no longer sticks to the back button and title; there is space between them now. Reload the page once after updating.*

- **Ruimte tussen de kop en de foto in een recept.** Open je een recept bij **Recepten zoeken**, bij een winkel (Albert Heijn, Jumbo, Picnic) of bij **Maaltijdboxen**, dan stond de foto direct onder de terugknop en de titel. Er zit nu 16 px ruimte tussen, net als bij een melding onder de kop.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#165](https://github.com/helmerzNL/MealieShopper/pull/165).

## v2.84.0

### Nieuw in 2.84.0

*English summary: the servings you set for a meal in Mealie's meal planner (Mealie v3.29.0 or newer) now carry over. The week menu shows them ("4 servings") and every amount is scaled to match: the ingredient list, the review list, the basket and the MCP tool `get_week_menu`. The check list you see before the basket is filled now shows a photo of the chosen product on every line, for Albert Heijn, Picnic and Jumbo; pick another product and the photo changes with it, tap the photo to see it large. Fixed: the same ingredient in two recipes of the week is now added up (2× 400 g spaghetti is 800 g) instead of counting only the first. Reload the page once after updating.*

- **Porties uit de planner van Mealie.** Sinds Mealie v3.29.0 heeft elke geplande maaltijd een eigen aantal porties. MealieShopper leest dat in (`recipeScale`) en rekent de hoeveelheden van die maaltijd om: in de lijst met ingrediënten, de controlelijst, het mandje en bij de MCP-tool `get_week_menu`, die per maaltijd ook de porties meegeeft. Het weekmenu toont onder elke maaltijd het aantal porties, bijvoorbeeld *4 porties · 9 ingrediënten*. Weet Mealie de porties van het recept niet, dan staat er alleen de schaal (*×1,5*). Een oudere Mealie geeft geen porties mee; dan blijft alles zoals het was. Wijzig je een maaltijd in MealieShopper, dan blijven de porties staan; kies je een ander recept, dan begint het, net als in Mealie, weer bij 1. Let op: de porties-knop op de receptpagina van Mealie verandert alleen wat je daar ziet en wordt niet bewaard; stel de porties in bij de maaltijd in de planner.
- **Een foto bij elk product in de controlelijst.** Na **Voeg toe aan … mandje** (Albert Heijn, Picnic of Jumbo) staat in **Controleer je mandje** bij elke regel de foto van het gekozen product, naast de keuzelijst, zodat je ziet of het het goede product is voordat het in je mandje gaat. Kies je een ander product, dan wisselt de foto mee; na *Zoek een ander product* ook. Bij Picnic is dit de enige manier om het product te zien, want Picnic heeft geen webpagina met het product (en dus geen link **Bekijk product**). Op een smal scherm staat de foto onder het vinkje, naast de keuzelijst en het aantal. Heeft een product geen foto of laadt hij niet, dan blijft het vak leeg en blijven de kolommen gelijk. De winkels gaven de foto al mee; de back-up verandert niet.
- **Tik op de foto voor de foto in het groot.** Een venster toont de foto zo groot als het scherm toelaat, met de naam, de verpakking en de prijs. Sluiten doe je met **Sluiten**, met Esc of door naast het venster te tikken. Albert Heijn en Picnic geven daarvoor een groter formaat mee (bij AH het formaat rond 800 beeldpunten, bij Picnic `large`), zodat de foto scherp blijft; bij Jumbo, of als het grote formaat niet laadt, toont het venster de gewone foto. Een product zonder foto kun je niet aantikken.
- **API**: de kandidaten van `POST /api/ah/cart/preview` en `POST /api/picnic/cart/preview` hebben een nieuw veld `imageLarge` (of `null`).

### Opgelost in 2.84.0

- **Gelijke ingrediënten werden niet opgeteld.** Kwam hetzelfde ingrediënt in twee recepten van de week voor, dan telde alleen de eerste regel mee: 2× 400 g spaghetti werd 400 g. Nu worden regels met hetzelfde ingrediënt en dezelfde eenheid opgeteld; met een andere eenheid (*1 ui* en *100 g ui*) blijven het aparte regels. Een regel zonder hoeveelheid (*peper*) staat er één keer in, en wijkt voor een regel van hetzelfde ingrediënt met een hoeveelheid.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull requests: [#163](https://github.com/helmerzNL/MealieShopper/pull/163) (issue [#158](https://github.com/helmerzNL/MealieShopper/issues/158)) en [#164](https://github.com/helmerzNL/MealieShopper/pull/164) (issue [#159](https://github.com/helmerzNL/MealieShopper/issues/159)).

## v2.83.0

### Nieuw in 2.83.0

*English summary: every recipe can now be planned, also from Albert Heijn, Jumbo, Picnic, HelloFresh and Marley Spoon: planning imports it into Mealie first, or reuses the recipe if Mealie already has it from the same source. Every recipe card, in Find recipes, Stores and Meal kits, has the same two buttons at the bottom: **View** and **Plan**, equally wide and lined up per row. **View** opens the recipe inside MealieShopper (this replaces "Open at AH"), with **Plan**, **Import** and a link to the store or to Mealie. Fixed: switching the language redraws everything again. Reload the page once after updating.*

- **Elk recept is in te plannen, ook van een winkel of maaltijdbox.** **Inplannen** bij een recept van Albert Heijn, Jumbo, Picnic, HelloFresh of Marley Spoon zet het recept bij het opslaan eerst in Mealie en dan in het weekmenu. Staat het al in Mealie, dan gebruikt MealieShopper dat recept in plaats van een tweede import: Mealie bewaart van elke import de bronlink, en daarop zoekt MealieShopper eerst (Allerhande op receptnummer, omdat de link met of zonder naam kan zijn opgeslagen). Lukt dat zoeken niet (een oudere Mealie), dan wordt er gewoon geïmporteerd. **Importeer** (zonder plannen) importeert zoals voorheen altijd.
- **Overal dezelfde knoppen op een receptkaart.** Bij Recepten zoeken, Winkels (Albert Heijn, Jumbo, Picnic) en Maaltijdboxen heeft elke kaart onderaan **Bekijken** en **Inplannen**, even breed en in een rij kaarten op dezelfde hoogte; op een smal scherm staan ze onder elkaar. Voorheen stonden de knoppen per bron anders (bij Mealie onder elkaar, bij AH een brede Importeer-knop en een losse link).
- **Bekijken binnen MealieShopper, ook bij Albert Heijn.** "Open bij AH" (en "Bij HelloFresh" of "Bij Marley Spoon" bij Maaltijdboxen) is **Bekijken** geworden: het recept met ingrediënten en bereiding opent in de app, met daar **Inplannen**, **Importeer** en de link **Bekijk bij Albert Heijn** (of de andere winkel), of **Open in Mealie** bij een eigen recept. Picnic heeft geen webpagina en dus geen link. Picnic, de bewaarde AH-recepten en Maaltijdboxen gebruiken nu hetzelfde detailscherm als Recepten zoeken.
- **Nieuwe API's**: `GET /api/ah/recipes/<id>` en `POST /api/ah/recipes/<id>/import` (Allerhande met dezelfde routes als de andere winkels), en `?reuse=1` op de importroutes van AH, Jumbo, Picnic, HelloFresh en Marley Spoon.

### Opgelost in 2.83.0

- **Een taalwissel tekende niet alles opnieuw.** De taalwissel riep een functie aan die sinds 2.8.0 niet meer bestond (`renderSearchResults`); de rest van de handler werd daardoor overgeslagen, zodat onder meer de inlogstatus, de AH-lijstjes en de bewaarde recepten in de oude taal bleven staan. Een test controleert nu dat de handler alleen bestaande functies aanroept.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#162](https://github.com/helmerzNL/MealieShopper/pull/162), vervolg op [#161](https://github.com/helmerzNL/MealieShopper/pull/161).

## v2.82.0

### Nieuw in 2.82.0

*English summary: you can now plan the week menu in MealieShopper. Every day of the week has **Add**: put a recipe from Mealie or a note ("Eating out") on a day, or let Mealie pick one with **Surprise me** following your meal plan rules. Tap a planned meal to move, change or remove it. Every change goes straight to Mealie; MealieShopper keeps no plan of its own. **Find recipes** now also searches your own recipes in Mealie (at the top, with **Plan**), and after every import the message has **Plan** too. The MCP server gets `search_mealie_recipes`, `plan_meal` and `remove_planned_meal`. Fixed: a week with a note without a recipe no longer breaks the week menu. Reload the page once after updating.*

- **Het weekmenu plannen in MealieShopper.** Het weekmenu toont altijd de hele week, met bij elke dag **Toevoegen**. Kies de dag, de maaltijd en een **Recept** (zoeken in je recepten in Mealie) of een **Notitie** zonder recept, zoals *Uit eten* of *Restjes*, met een optionele toelichting. Tik op een geplande maaltijd om de dag, de maaltijd of het recept te wijzigen, of om haar te verwijderen (het recept blijft in Mealie staan).
- **Verras me.** Laat Mealie zelf een recept kiezen volgens de planregels van je huishouden; het staat meteen in het weekmenu. Past er geen recept, dan zegt de app dat.
- **Alles gaat meteen naar Mealie.** MealieShopper bewaart zelf geen planning: elke wijziging gaat direct naar de API van Mealie (`/api/households/mealplans`) en daarna wordt de week opnieuw opgehaald. Wat je in Mealie plant staat hier, en andersom. Er is geen nieuwe tabel; de back-up verandert niet.
- **Iedereen die is ingelogd mag plannen**, net als recepten importeren. In Mealie staat de maaltijd op naam van de gebruiker van de token.
- **Snack, drankje en nagerecht** kun je kiezen zodra je Mealie 3.7 of nieuwer hebt; MealieShopper vraagt de versie aan Mealie en toont bij een oudere Mealie alleen ontbijt, lunch, diner en bijgerecht.
- **Recepten zoeken zoekt ook in Mealie.** Je eigen recepten staan als eerste blok, met **Bekijken** (ingrediënten en bereiding, en **Open in Mealie**) en **Inplannen** in plaats van **Importeer**, en `Meer recepten` voor de volgende pagina.
- **Inplannen na een import.** Bij Recepten zoeken, Maaltijdboxen, Winkels en URL importeren staat in de melding na een import nu **Inplannen**; het venster opent met het nieuwe recept al gekozen.
- **AI-assistenten (MCP)** kunnen zoeken in je recepten in Mealie (`search_mealie_recipes`), een maaltijd plannen (`plan_meal`) en een geplande maaltijd weghalen (`remove_planned_meal`). `get_week_menu` geeft per maaltijd het id en een eventuele notitie. `remove_planned_meal` is de enige tool die iets weghaalt en is gemarkeerd als destructief; de beheerder kan elke tool uitzetten onder **Profiel, AI-apps**.
- **Nieuwe API's**: `GET /api/mealplan/options`, `POST /api/mealplan`, `PATCH` en `DELETE /api/mealplan/<id>`, `POST /api/mealplan/random`, `GET /api/mealie/recipes?q=&page=` en `GET /api/mealie/recipes/<slug>`.
- **Onderzoek** naar wat de API van Mealie toelaat en waarom MealieShopper geen eigen planning bijhoudt: [`docs/onderzoek-weekmenu-plannen.md`](docs/onderzoek-weekmenu-plannen.md).

### Opgelost in 2.82.0

- **Een notitie zonder recept liet het hele weekmenu mislukken.** Mealie geeft bij zo'n maaltijd `"recipe": null`; MealieShopper las dat als een recept en gaf `'NoneType' object has no attribute 'get'`. Zo'n week laadt nu gewoon.
- **Net na middernacht begon de week een dag te vroeg** (tot 1 uur, met zomertijd tot 2 uur). De datums werden in UTC berekend; nu in je eigen tijdzone. Bij plannen telt elke dag.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

- Pull request: [#161](https://github.com/helmerzNL/MealieShopper/pull/161).

## v2.81.0

### Nieuw in 2.81.0

*English summary: MealieShopper can now be installed as a Home Assistant add-on. Add `https://github.com/helmerznl/mealieshopper` as an add-on repository and install **MealieShopper** (stable) or **MealieShopper (beta)**; the options on the Configuration tab are the environment variables of the app, and Home Assistant includes the data folder in its backups. The image has a new entrypoint that turns the add-on options into environment variables (it does nothing outside Home Assistant), and a beta image now knows it is a beta.*

- **MealieShopper als Home Assistant-add-on.** Voeg in Home Assistant onder **Instellingen > Add-ons > Add-on store > Repositories** het adres `https://github.com/helmerznl/mealieshopper` toe en installeer **MealieShopper**. De add-on draait hetzelfde image als de Docker-versie (`linux/amd64` en `linux/arm64`), op poort `5959`, met de data-map `/data`: Home Assistant neemt de database en `secret.key` dus mee in zijn back-ups. De app heeft een eigen inlog, passkeys en een MCP-server en heeft daarom geen ingress. Een `watchdog` op `/healthz` start de add-on weer na een fabrieksreset.
- **Opties op het tabblad Configuratie**, allemaal optioneel en in het Nederlands en Engels uitgelegd: het Mealie-adres en -token, het publieke adres, het geheim, de API-sleutels van Claude en ChatGPT, het Picnic-land, de dagelijkse aanbiedingenrun, de MCP-server, de updatecheck, het auditlog en de instellingen voor passkeys. Het zijn dezelfde waarden als de omgevingsvariabelen van de app; wat je in de app instelt, gaat nog steeds voor.
- **Een tweede add-on voor de beta**, **MealieShopper (beta)**, met een eigen data-map en poort `5960`, in de repository `https://github.com/helmerznl/mealieshopper#beta`. `python scripts/ha_addon.py` maakt hem uit de stabiele add-on en zet de versie uit `VERSION` in beide; een test faalt als ze afwijken.
- **Nieuw startpunt van het image** (`docker-entrypoint.py`): zet de opties van de add-on om in omgevingsvariabelen en start dan gunicorn. Buiten Home Assistant, zonder `/data/options.json`, verandert er niets.
- **Een beta-image weet dat het beta is.** De workflow geeft de bouw de lijn mee (`BUILD_TAG`: `beta` of `latest`), zodat de **beta**-badge ook zonder `MEALIESHOPPER_TAG` verschijnt, bijvoorbeeld in de beta-add-on. `MEALIESHOPPER_TAG` in `docker-compose.yml` of `docker run -e` gaat nog steeds voor.

## v2.80.2

### Nieuw in 2.80.2

*English summary: server messages such as "AH account not linked" and the API errors of the app now follow the chosen language (before, many stayed Dutch in the English interface); long recipe names in the week menu wrap instead of being cut off; all README screenshots were made again in the new look, with a script to regenerate them (`scripts/screenshots`). Reload the page once after updating.*

- **Meldingen van de server staan in de gekozen taal.** "AH account niet gekoppeld", "Mealie is niet gekoppeld" en de foutmeldingen van de API (zoals "Zoekterm ontbreekt" of "Importeren in Mealie mislukt") bleven in het Nederlands staan als de interface op Engels stond. Ze volgen nu de taalkeuze, net als de meldingen van Jumbo en Picnic, waarvan de Engelse tekst bovendien naar het goede menu verwijst (**Settings > Link**, niet *Admin*).
- **Lange receptnamen in het weekmenu** worden afgebroken in plaats van afgesneden.
- **Alle screenshots in de README's** zijn opnieuw gemaakt in de nieuwe stijl, in het Nederlands en het Engels, met het huidige menu en de nieuwe schermen. Ze komen uit een script met nagebootste gegevens en zelfgetekende illustraties (`scripts/screenshots`, zie de README); zo zijn ze bij een volgende release in één commando te vernieuwen.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

## v2.80.1

### Nieuw in 2.80.1

*English summary: the page no longer jumps sideways when a screen is tall enough to need a scrollbar; the space for the scrollbar is always reserved. Reload the page once after updating.*

- **De interface verspringt niet meer als de scrollbalk verschijnt.** Werd een scherm hoger dan het venster, dan kwam de scrollbalk erbij en schoof alles een stukje naar links (bij 15 px). De ruimte voor de scrollbalk blijft nu altijd gereserveerd (`scrollbar-gutter: stable`, in oudere browsers `overflow-y: scroll`), zodat menu, kop en inhoud op hun plek blijven bij het wisselen van scherm.
- **Nieuw adres voor de scripts en stijlen** (`MEALIESHOPPER_ASSET_VERSION`); ververs de pagina één keer.

## v2.80.0

### Nieuw in 2.80.0

*English summary: a complete visual refresh. The interface keeps the basil-green MealieShopper look but gets depth, softer navigation, a segmented sub-menu, polished buttons and fields, a "Today" marker in the week menu, clearer price and offer labels and calmer motion. No features, data or settings changed. Reload the page once after updating.*

- **Een professionelere, rustigere interface.** Het palet, de lettertypen en de indeling blijven; wat verandert is de afwerking. Kaarten, knoppen en menu's hebben nu subtiele diepte (drie schaduwniveaus), de balk bovenaan is mat glas dat meebeweegt met de pagina, en de hele app gebruikt dezelfde korte, rustige overgangen. Alle bewegingen staan uit bij *minder beweging* in het apparaat.
- **Navigatie.** Het hoofdmenu links heeft een lichte groene vulling en een streepje bij het actieve onderdeel in plaats van een zwaar groen blok. De tabs eronder (bijvoorbeeld Weekmenu / Recepten zoeken) zijn een segmentbediening: een zacht spoor met een oplichtend actief segment. De onderste menubalk op een telefoon blijft zoals hij was.
- **Weekmenu.** De dag van vandaag heeft een groene rand en het label **Vandaag**, zodat je in één blik ziet waar je bent. De dagkaarten hebben een rustiger kopje.
- **Aanbiedingen en lijstjes.** De prijs staat groter en vet, met de oude prijs er klein doorgestreept naast; *Bonus* en kortingen zijn een duidelijke pil. Productfoto's staan op een witte tegel met een dunne rand. Categoriefilters hebben dezelfde afwerking als de knoppen.
- **Knoppen, velden en meldingen.** Knoppen hebben een fijne rand van licht en geven een indrukeffect; invoervelden krijgen een duidelijke groene focusring met halo; keuzelijsten hebben één eigen pijltje. Meldingen hebben links een gekleurde balk voor het soort melding, en er staat nog steeds altijd uitleg bij.
- **Inloggen en installatiehulp.** De inlogkaart staat gecentreerd met een zachte groene gloed erachter; de installatiehulp heeft een lijn tussen de stappen, groen voor wat klaar is.
- **Op een telefoon** is de balk bovenaan compacter (zonder de slogan) en staan de taalkiezer en de knop voor donker en licht op dezelfde regel als het logo.
- **Donker thema** kreeg eigen, diepere schaduwen en een lichte rand bovenaan kaarten, zodat de lagen ook in het donker te onderscheiden zijn.
- **Toegankelijkheid blijft gelijk of beter:** alle kleurparen halen nog steeds AA (4,5 : 1), de placeholders hebben een eigen kleur van 4,8 : 1, de focusring blijft zichtbaar en kaarten tillen alleen op bij een muis.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen). De stijlen hebben een nieuw adres (`MEALIESHOPPER_ASSET_VERSION`), dus de browser haalt ze vanzelf op; zie je toch nog het oude uiterlijk, ververs dan hard (Ctrl+Shift+R, op een Mac Cmd+Shift+R).
- Er zijn geen wijzigingen in gegevens, back-up, Compose- of `.env`-bestanden.

*English summary: reload the page once (installed app: close and reopen; hard-refresh if the old look stays). Nothing changed in data, backup, Compose or `.env` files.*

## v2.79.4

### Sinds de vorige stabiele versie (v2.77.1)

Deze release brengt `main` van v2.77.1 naar v2.79.4. De tussenliggende versies (2.78.0 tot en met 2.79.3) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.77.1: there is a new Meal kits tab with the public weekly menus of HelloFresh and Marley Spoon Netherlands. You choose the provider in a drop-down menu in the bar (like the store under Stores), browse per week, filter, search the weeks you have looked at, open a recipe at the provider, or import it into Mealie with one click (ingredients, method and nutrition). No account or link is needed. Search recipes now also searches those menus, for this week and the next three weeks. The "AI review of links" block under Settings > Link can be collapsed like the store blocks. Importing from HelloFresh and Marley Spoon gives clearer messages, retries once without photo and nutrition, and every attempt is in the audit log. Everything is unofficial: the providers have no public API and may change their sites. Reload the page once after updating.*

- **Nieuw tabblad Maaltijdboxen (2.78.0, 2.79.0, 2.79.1).** Naast Winkels staat een tabblad met de openbare weekmenu's van **HelloFresh** en **Marley Spoon** Nederland: bladeren per week (vorige, volgende, deze week), filteren op naam, kop, keuken of label, en met **Zoek in alle weken** zoeken in de weken die je al hebt bekeken. Je kiest de aanbieder in een uitklapmenu in de balk bovenaan, zoals de winkelkeuze bij Winkels. Elke kaart heeft de foto, de kooktijd en twee knoppen: **Bij HelloFresh** of **Bij Marley Spoon** opent het recept op hun site, en **Importeer** zet het recept met ingrediënten, bereiding en voedingswaarden in Mealie.
- **Geen account en geen koppeling.** MealieShopper leest alleen openbare gegevens: bij HelloFresh het weekmenu en de schema.org-gegevens van de receptpagina, bij Marley Spoon het menu uit hetzelfde GraphQL-eindpunt als hun eigen menupagina en voor een import het recept met het bezoekerstoken dat elke receptpagina aan iedere bezoeker geeft (nergens bewaard of gelogd). Verzoeken gaan één voor één, met een pauze ertussen en met `MealieShopper` in de User-Agent; een weekmenu wordt 6 uur bewaard. Dit is onofficieel: een aanbieder kan zijn site wijzigen, waarna het menu of de import tijdelijk niet werkt. De recepten en foto's blijven van de aanbieders.
- **Recepten zoeken zoekt ook bij HelloFresh en Marley Spoon (2.79.3).** Onder Recepten > Recepten zoeken staan twee extra blokken naast Albert Heijn, Jumbo en Picnic, met dezelfde knoppen **Bekijken** (ingrediënten en bereiding in het scherm) en **Importeer**. Bij het zoeken worden het menu van deze week en van de drie weken erna opgehaald als ze er nog niet zijn; de eerste zoekopdracht duurt daardoor even.
- **Importeren geeft een bruikbare melding en een tweede kans (2.78.1, 2.78.2).** Bij een mislukte import staat welke status Mealie gaf (het antwoord van Mealie zelf blijft in het logboek), weigert Mealie het recept met een 4xx dan volgt een tweede poging zonder foto en voedingswaarden (bij HelloFresh daarna nog via de pagina), en bij een serverfout (5xx) wordt niet opnieuw geprobeerd omdat het recept dan al kan bestaan. Antwoordt Mealie niet binnen 60 seconden, dan staat dat er, met de waarschuwing dat het recept toch kan zijn aangemaakt. Elke poging staat in het auditlog (Instellingen > Auditlog, filters **HelloFresh** en **Marley Spoon**, actie **Mealie import**).
- **Het blok AI-beoordeling van koppelingen is inklapbaar (2.79.4).** Onder Instellingen > Koppelen heeft het blok een kop met een pijltje, net als Mealie en de winkels. Staat er al een API-sleutel (in de app of als omgevingsvariabele), dan staat het blok ingeklapt; zonder sleutel staat het open. Een keuze die je zelf maakt blijft staan, en de melding na het opslaan blijft zichtbaar.
- **Het wisselen van aanbieder werkt met de nieuwste scripts (2.79.2).** De scripts hebben een nieuw adres (`MEALIESHOPPER_ASSET_VERSION`), zodat een browser het oude script uit de cache niet blijft gebruiken.
- **README's en screenshots bijgewerkt:** nieuwe schermen voor Maaltijdboxen, het zoeken in de menu's van de aanbieders en de inklapbare blokken onder Instellingen, in het Nederlands en het Engels.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen). Zie je het tabblad Maaltijdboxen, het zoekblok van HelloFresh of Marley Spoon of het inklapbare AI-blok niet, ververs dan hard (Ctrl+Shift+R, op een Mac Cmd+Shift+R).
- De database krijgt de tabellen `hellofresh_menus` en `marleyspoon_menus` vanzelf. Die zitten bewust niet in de back-up: het zijn herlaadbare kopieën van openbare menu's, en een back-up van een eerdere versie laat zich gewoon terugzetten.
- Op een telefoon staan er in de onderste balk nu zes tabs plus je profiel.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: reload the page once after updating (installed app: close and reopen; hard-refresh if a new screen is missing). The database gets the tables `hellofresh_menus` and `marleyspoon_menus` by itself; they are deliberately not in the backup because they are reloadable copies of public menus. On a phone the bottom bar now has six tabs plus your profile. Compose and `.env` files are unchanged.*

### Nieuw in 2.79.4

- **Het blok AI-beoordeling van koppelingen is inklapbaar.** Zie hierboven: open zonder API-sleutel, dicht met een sleutel, en een eigen keuze blijft staan.
- **Nieuw adres voor de scripts** (`MEALIESHOPPER_ASSET_VERSION`), zodat de browser het nieuwe script meteen ophaalt.

## v2.79.3

### Nieuw in 2.79.3

- **Recepten zoeken zoekt nu ook bij HelloFresh en Marley Spoon.** Onder Recepten > Recepten zoeken staan naast Albert Heijn, Jumbo en Picnic twee blokken, **HelloFresh** en **Marley Spoon**, met de recepten uit hun weekmenu's die bij je zoekwoord passen: naam, kop, keuken of label. Er is geen koppeling of account voor nodig. Elke kaart heeft dezelfde knoppen als de andere winkels: **Bekijken** toont de ingrediënten en de bereiding in het scherm, en **Importeer** zet het recept in Mealie.
- **Welke weken.** Bij het zoeken worden het menu van deze week en van de drie weken erna opgehaald als ze er nog niet zijn (ongeveer vier verzoeken per aanbieder, één voor één en met een pauze; de eerste zoekopdracht duurt daardoor even), en 6 uur bewaard. Daarna zoekt het direct. Lukt het ophalen van een week niet, dan wordt die overgeslagen; lukt het helemaal niet en is er niets bewaard, dan staat er een melding bij die aanbieder en blijven de andere winkels gewoon staan. In het tabblad Maaltijdboxen blijft **Zoek in alle weken** alleen zoeken in de weken die je al hebt bekeken.
- **Nieuw adres voor de scripts** (`MEALIESHOPPER_ASSET_VERSION`), zodat de browser het nieuwe zoekscript meteen ophaalt.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen). Zie je het zoekblok van HelloFresh of Marley Spoon niet, ververs dan hard (Ctrl+Shift+R, op een Mac Cmd+Shift+R).
- Er zijn geen nieuwe tabellen en de Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: Search recipes now also searches HelloFresh and Marley Spoon. Two new blocks next to Albert Heijn, Jumbo and Picnic show the recipes from their weekly menus that match your search (name, headline, cuisine or label); no link or account is needed. Each card has the same buttons as the other stores: View shows the ingredients and the method on screen, Import puts the recipe in Mealie. While searching, the menus of this week and the next three weeks are fetched if they are not there yet (about four requests per provider, one at a time with a pause, so the first search takes a moment) and kept for 6 hours; a week that fails is skipped, and if nothing at all can be fetched and nothing is stored, that provider shows a message while the other stores stay. "Search all weeks" in the Meal kits tab still only searches the weeks you have looked at. The scripts have a new address so the browser fetches the new search script right away. Reload the page once (installed app: close and reopen).*

## v2.79.2

### Gerepareerd in 2.79.2

- **Het wisselen van box bij Maaltijdboxen deed niets.** Na de update naar 2.79.1 kon de browser (of de geïnstalleerde app) nog het oude script van het tabblad uit de cache gebruiken, omdat het adres van de scripts niet was veranderd. Dat oude script zocht de vroegere keuzelijst, die er niet meer is, en stopte met een fout: het uitklapmenu ging open, maar een andere aanbieder kiezen liet het menu ongewijzigd. De scripts en stijlen hebben nu een nieuw adres (`MEALIESHOPPER_ASSET_VERSION`), zodat de browser ze opnieuw ophaalt.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen). Staat het wisselen daarna nog stil, ververs dan hard (Ctrl+Shift+R, op een Mac Cmd+Shift+R).
- Er zijn geen nieuwe tabellen en de Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: switching the provider under Meal kits did nothing after updating to 2.79.1: the browser (or installed app) could still use the old tab script from its cache because the script address had not changed. That old script looked for the former drop-down list, which no longer exists, and stopped with an error, so the menu opened but choosing another provider changed nothing. The scripts and stylesheets now have a new address (`MEALIESHOPPER_ASSET_VERSION`) so the browser fetches them again. Reload the page once (installed app: close and reopen); if it still does not switch, hard-refresh.*

## v2.79.1

### Nieuw in 2.79.1

- **Marley Spoon-recepten importeren in Mealie.** Elke kaart bij Marley Spoon heeft nu naast **Bij Marley Spoon** ook de knop **Importeer**, die het recept met ingrediënten, bereiding en voedingswaarden in Mealie zet (twee personen, zoals de menupagina). Voor een import haalt MealieShopper eerst de receptpagina op en vraagt het recept dan op met het bezoekerstoken dat die pagina aan iedere bezoeker geeft, zoals de site zelf doet. Dat token wordt nergens bewaard of gelogd. Weigert Mealie het recept, dan volgt één tweede poging zonder foto en voedingswaarden; Mealie de pagina zelf laten lezen helpt hier niet, want die heeft geen receptgegevens. Elke poging staat in het auditlog (filter **Marley Spoon**, actie **Mealie import**). Dit is onofficieel: Marley Spoon kan de eindpunten wijzigen, waarna de import tijdelijk niet werkt. Dit vervangt de opmerking bij v2.79.0 dat importeren niet kan.
- **De aanbiederskeuze is een uitklapmenu in de balk**, zoals de winkelkeuze bij Winkels. De kop **Menu van …** is vervallen en de knop **Verversen** staat nu naast **Deze week**.
- **Gedeelde importlogica.** De pogingen, foutteksten en auditregel van een import staan nu in `mealieshopper/mealkit_import.py` en worden door HelloFresh en Marley Spoon gedeeld; het gedrag van HelloFresh is ongewijzigd.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen): het tabblad Maaltijdboxen heeft een nieuwe opmaak.
- Er zijn geen nieuwe tabellen en de Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: Marley Spoon recipes can now be imported into Mealie with an Import button next to "At Marley Spoon". An import first loads the recipe page and then asks for the recipe with the visitor token that page gives every visitor, as the site itself does; the token is never stored or logged. If Mealie refuses the recipe there is one second attempt without photo and nutrition, and every attempt is in the audit log. It is unofficial and Marley Spoon may change it. This replaces the note under v2.79.0 that importing is not possible. The provider choice is now a drop-down in the bar, like the store choice under Stores (the "Menu of …" heading is gone, Refresh sits next to This week). The import logic is shared by HelloFresh and Marley Spoon in `mealieshopper/mealkit_import.py`; HelloFresh behaves as before. Reload the page once.*

## v2.79.0

### Nieuw in 2.79.0

- **Marley Spoon in het tabblad Maaltijdboxen.** Boven in het tabblad staat nu een keuzelijst **Aanbieder** met HelloFresh en Marley Spoon. Van Marley Spoon Nederland zie je het openbare weekmenu: bladeren per week, filteren op naam, kop of label, en met **Zoek in alle weken** zoeken in de weken die je al hebt bekeken. Elke kaart heeft de foto, de kooktijd en de knop **Bij Marley Spoon**, die het recept op hun site opent.
- **Importeren in Mealie kan bij Marley Spoon niet.** De ingrediënten en stappen staan op hun site niet in de pagina zelf en zijn alleen te lezen met een token dat de pagina per bezoek krijgt. Dat bootsen we niet na, dus er is bij Marley Spoon geen knop **Importeer**. Bij HelloFresh is niets veranderd.
- **Hoe het menu wordt gelezen.** De menupagina van Marley Spoon vraagt het menu zelf op bij een GraphQL-eindpunt dat zonder inloggen en zonder sleutel antwoordt; MealieShopper doet dezelfde vraag, voor één week tegelijk, met een pauze tussen verzoeken en met `MealieShopper` in de User-Agent. Het menu van een week wordt 6 uur bewaard. Dit is onofficieel: Marley Spoon kan het eindpunt wijzigen, waarna het menu tijdelijk niet laadt. Recepten en foto's blijven van Marley Spoon.
- **Het verkeer met Marley Spoon staat in het auditlog** (Beheer > Auditlog, filter **Marley Spoon**).
- **Dinnerly en de Allerhande Box zijn onderzocht en nog niet toegevoegd.** `dinnerly.nl` stuurt door naar Marley Spoon en het Nederlandse Dinnerly-menu is leeg; AH laat geautomatiseerde verzoeken aan zijn Allerhande-pagina's niet toe en de AH-API toont geen box-menu. Zie `docs/onderzoek-marleyspoon.md` en `docs/onderzoek-allerhande-box.md`.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen): het script van het tabblad heet nu `mealkits.js`.
- De database krijgt de tabel `marleyspoon_menus` vanzelf. Die zit bewust niet in de back-up: het is een herlaadbare kopie van openbare menu's.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: the Meal kits tab now has a Provider list with HelloFresh and Marley Spoon. For Marley Spoon Netherlands you see the public weekly menu (browse per week, filter, search the weeks you have looked at) and an "At Marley Spoon" button; importing into Mealie is not possible because their pages only show ingredients and steps through a per-visit token, which we do not imitate. The menu is read from the same unauthenticated GraphQL endpoint their own menu page uses, one week at a time, sparingly and with its own User-Agent, and cached for 6 hours. It is unofficial and Marley Spoon may change it. Traffic appears in the audit log. Dinnerly (redirects to Marley Spoon, empty Dutch menu) and the Allerhande Box (AH blocks automated page requests, no box menu in its API) were researched and not added. Reload the page once: the tab's script is now mealkits.js.*

## v2.78.2

### Gerepareerd in 2.78.2

- **Elke HelloFresh-import staat in het auditlog.** Onder Beheer > Auditlog (filter **HelloFresh**) komt bij elke poging een regel **Mealie import**, ook als het misgaat: met de status van Mealie, de duur en per poging wat er gebeurde (volledig, zonder foto en voedingswaarden, via de pagina). Verzoeken aan Mealie zelf staan niet los in het log (het adres van Mealie verschilt per installatie); alleen deze importpoging wordt vastgelegd. Het verkeer met hellofresh.nl stond er al in.
- **De melding zegt wat er misging.** Antwoordt Mealie niet binnen 60 seconden, dan staat er dat, met de waarschuwing dat het recept toch kan zijn aangemaakt (kijk in Mealie voordat je het opnieuw probeert). Is Mealie niet bereikbaar, dan staat dat er. Bij een onverwachte fout noemt de melding alleen de soort fout (bijvoorbeeld *KeyError*), nooit de inhoud van het antwoord van Mealie; die staat in het serverlogboek.

*English summary: every HelloFresh import attempt is now in the audit log (Administration > Audit log, HelloFresh filter) as "Mealie import", also when it fails: with Mealie's status, the duration and what happened per attempt. Requests to Mealie itself are not logged separately because its address differs per installation. The message now says what went wrong: no answer within 60 seconds (the recipe may have been created anyway), Mealie not reachable, or the kind of unexpected error, never the content of Mealie's answer.*

## v2.78.1

### Gerepareerd in 2.78.1

- **Importeren van een HelloFresh-recept in Mealie geeft een bruikbare melding en een tweede kans.** De import kon eindigen op *Importeren in Mealie mislukt*, zonder te zeggen waarom. Nu staat er welke status Mealie gaf (bijvoorbeeld *Mealie antwoordde met status 400*); het antwoord van Mealie zelf blijft alleen in het logboek. Weigert Mealie het recept met een 4xx, dan probeert MealieShopper het nog twee keer: zonder foto en voedingswaarden, en daarna door Mealie de HelloFresh-pagina zelf te laten lezen. Bij een serverfout (5xx) wordt niet opnieuw geprobeerd, omdat Mealie het recept dan al kan hebben aangemaakt.
- **Schonere gegevens naar Mealie.** De voedingswaarden bevatten alleen nog gevulde, bekende velden (geen lege waarden of `servingSize` uit de HelloFresh-pagina), en de foto gaat als vaste JPG (`f_jpg`) in plaats van `f_auto`, dat soms WebP of AVIF geeft.
- Staat een recept na een mislukte import toch in Mealie, of blijft de melding terugkomen? Meld dan de status uit de melding en de logregel `HelloFresh-recept ...: import in Mealie ... mislukt`.

*English summary: importing a HelloFresh recipe into Mealie could end in "Importing into Mealie failed" without saying why. The message now shows the status Mealie gave (Mealie's own answer stays in the log only). If Mealie refuses the recipe with a 4xx, MealieShopper tries twice more: without photo and nutrition, then by letting Mealie read the HelloFresh page itself; after a 5xx it does not retry because Mealie may already have created the recipe. Nutrition values are cleaned (only filled, known fields) and the photo is sent as a plain JPG.*

## v2.78.0

### Nieuw in 2.78.0

- **Nieuw tabblad Maaltijdboxen met het menu van HelloFresh.** Naast Winkels staat nu een eigen tabblad met de openbare weekmenu's van HelloFresh Nederland: bladeren per week (vorige, volgende, deze week), filteren op naam, kop, keuken of label, en met **Zoek in alle weken** zoeken in de weken die je al hebt bekeken. Elke kaart heeft de foto, de kooktijd en twee knoppen: **Bij HelloFresh** opent het recept op hun site en **Importeer** zet het recept met ingrediënten, bereiding en voedingswaarden in Mealie.
- **Geen account en geen koppeling.** MealieShopper leest alleen de pagina's die ook zonder inloggen openstaan: het weekmenu en de receptpagina, via de schema.org-gegevens die HelloFresh er zelf op zet. Er wordt niets geschreven en er staat geen wachtwoord of token in de database.
- **Zuinig en eerlijk.** Een weekmenu wordt 6 uur bewaard in de database, zodat HelloFresh niet bij elke weergave wordt gevraagd (de knop **Verversen** haalt het opnieuw op). Verzoeken gaan één voor één, met een pauze ertussen en met `MealieShopper` in de User-Agent, en alleen naar paden die `robots.txt` van HelloFresh niet uitsluit. Een receptadres wordt streng gecontroleerd voordat de server er iets naartoe stuurt.
- **Het verkeer met HelloFresh staat in het auditlog** (Beheer > Auditlog, filter **HelloFresh**).
- Dit is een onofficiële bron: HelloFresh kan de pagina's wijzigen, waarna het menu of de import tijdelijk niet werkt. De recepten en foto's blijven van HelloFresh. Het onderzoek erachter staat in `docs/onderzoek-hellofresh.md`.

#### Goed om te weten bij het bijwerken

- **Laad de pagina één keer opnieuw** (in de geïnstalleerde app: sluiten en weer openen) als het tabblad Maaltijdboxen niet vanzelf verschijnt; er is een nieuw script.
- De database krijgt de tabel `hellofresh_menus` vanzelf. Die zit bewust niet in de back-up: het is een herlaadbare kopie van openbare menu's.
- Op een telefoon staan er in de onderste balk nu zes tabs plus je profiel.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: a new Meal kits tab next to Stores shows the public weekly menus of HelloFresh Netherlands. Browse by week, filter, search the weeks you have looked at, open a recipe at HelloFresh or import it into Mealie with one click. No account or link is needed: only public pages are read, sparingly and with its own User-Agent, and menus are cached for 6 hours. It is unofficial, so HelloFresh may change its pages; recipes and photos remain HelloFresh's. Traffic to HelloFresh appears in the audit log.*

## v2.77.1

### Sinds de vorige stabiele versie (v2.74.0)

Deze release brengt `main` van v2.74.0 naar v2.77.1. De tussenliggende versies (2.75.0 tot en met 2.77.0) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.74.0: the Offers screen shows the period the offers are valid for at the top, for AH Bonus and for Jumbo (for example "Valid Mon 26 Oct to Sun 1 Nov"), and ongoing AH offers such as "5% volume voordeel" no longer show a bogus "until 31 Dec"; there is a new Price history tab where you search for a product and see in a chart how often it was on offer and at what price; and the Python dependencies were updated (Flask 3.1.3, PyJWT 2.15.0, requests 2.33.0). Rebuild the container.*

- **De looptijd van de AH Bonus staat bovenaan de aanbiedingen (2.75.0).** Bijvoorbeeld *Geldig ma 26 okt t/m zo 1 nov*: de looptijd die de meeste aanbiedingen delen, uit de begin- en einddatum die AH per aanbieding meegeeft. Doorlopende aanbiedingen als *5% volume voordeel* (bij AH met einddatum 31-12-2999) hebben geen einddatum meer en stonden eerst ten onrechte met *t/m 31 dec* op de kaart.
- **De looptijd van de Jumbo-aanbiedingen (2.76.0).** Jumbo geeft per promotie een begin en een einde; die staan nu bovenaan en op elke kaart, en een verlopen Jumbo-aanbieding verdwijnt na de einddatum uit de lijst. Picnic geeft geen datums.
- **Nieuw subtab Prijsgeschiedenis bij Aanbiedingen (2.77.0 en 2.77.1).** Zoek een product en zie in een grafiek elke keer dat het in de aanbieding was, voor welke prijs en in welke week, met de gewone prijs als stippellijn, de laagste en hoogste actieprijs en een tabel met dezelfde periodes. De geschiedenis bewaart nu ook de begindatum van de winkel, en dezelfde actie in een nieuwe week telt als een nieuwe keer. Prijslabels in de grafiek vallen niet meer over elkaar (2.77.1).
- **Afhankelijkheden bijgewerkt:** Flask 3.1.3, PyJWT 2.15.0 en requests 2.33.0 (Dependabot).

#### Goed om te weten bij het bijwerken

- **Bouw de container opnieuw:** `requirements.txt` heeft nieuwe versies van Flask, PyJWT en requests. Haal je de image `:latest` op, dan zit dat erin; bouw je zelf, bouw dan opnieuw.
- **Synchroniseer de aanbiedingen één keer opnieuw** (Instellingen, Koppelen): een kopie en geschiedenis van vóór deze versie hebben de begindatum van de winkel nog niet. De database krijgt de nieuwe kolommen (`offers.valid_from` en `offer_history.valid_from`) vanzelf, en een back-up van een eerdere versie laat zich gewoon terugzetten.
- **Het scherm Aanbiedingen heeft een nieuw script.** Laad de pagina één keer opnieuw als het tabblad Prijsgeschiedenis niet vanzelf verschijnt (in de geïnstalleerde app: sluiten en weer openen).
- Compose- of `.env`-bestanden zijn niet gewijzigd.

*English summary: In the Price history chart the price labels no longer overlap each other, the bars or the "Regular price" text. A label goes above its bar, or else below it, or a line higher or lower; if it fits nowhere it is left out (the price is still in the hover text and the table).*

### Opgelost in 2.77.1

- **Prijslabels in de grafiek van de Prijsgeschiedenis vielen over elkaar**, vooral bij een product dat vaak en kort na elkaar in de aanbieding was en op een smal scherm. Een label staat nu boven zijn balk, anders eronder, anders een regel hoger of lager, en raakt geen ander label, geen balk en niet de tekst *Gewone prijs*. Past het nergens, dan blijft het label weg; de prijs staat altijd nog in de hovertekst van de balk en in de tabel eronder.
- Compose- of `.env`-bestanden zijn niet gewijzigd. Laad de pagina één keer opnieuw als de grafiek er nog hetzelfde uitziet.

## v2.77.0

*English summary: The Offers screen has a new Price history tab. Search for a product and see in a chart how often it was on offer and at what price: every time is a bar over the store's validity period, with a dashed line for the regular price and an arrow for an offer that is still running. The history now also keeps the start date of each offer, so the bars show the real offer week; the same deal in a new week counts as a new time.*

### Nieuw in 2.77.0

- **Nieuw subtab Prijsgeschiedenis bij Aanbiedingen.** Zoek een product van de gekozen winkel (AH, Picnic of Jumbo) en kies het uit de lijst. De grafiek toont elke keer dat het in de aanbieding was als een balk op de actieprijs, over de looptijd van de aanbieding; een aanbieding die nog loopt eindigt in een pijl, en een stippellijn geeft de gewone prijs. Daarboven staan het aantal keren, de laagste en de hoogste actieprijs, de gewone prijs en wanneer het product voor het laatst is gezien; eronder staat een tabel met dezelfde periodes (ook voor schermlezers). De tab werkt in licht en donker en op een telefoon. Hij gebruikt de bestaande route `GET /api/offers/history`, die ook voor alle ingelogde gebruikers openstaat.
- **De geschiedenis bewaart nu de begindatum van de aanbieding** (`valid_from` in `offer_history`, wordt vanzelf toegevoegd). De balken tonen daardoor de echte week van de winkel in plaats van het moment dat MealieShopper het product toevallig zag. De route geeft per periode `validFrom`, `start`, `end` en `open`, en per product `regularPrice` en `highestPrice`.
- **Dezelfde actie in een nieuwe week telt als een nieuwe keer.** Begint de looptijd op een andere dag dan die van de open periode, dan sluit MealieShopper die en begint een nieuwe, ook als prijs en label gelijk zijn. Zo klopt *3 keer in de aanbieding* ook bij een actie die elke week terugkomt.

#### Goed om te weten bij het bijwerken

- **De geschiedenis van vóór deze versie heeft nog geen begindatum van de winkel.** Die rijen tonen de balk van de eerste tot de laatste synchronisatie; nieuwe synchronisaties vullen de begindatum aan zonder iets op te splitsen. Een back-up van een eerdere versie laat zich gewoon terugzetten.
- De pagina laadt een nieuw script (`offers_history.js`); laad de pagina één keer opnieuw als de tab niet vanzelf verschijnt.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.76.0

*English summary: Jumbo offers now carry their period too. Jumbo gives each promotion a start and an end, so the Offers screen can show "Valid Wed 21 Oct to Tue 27 Oct" above the Jumbo offers, like it does for AH Bonus. The Jumbo explorer asks for the real fields.*

### Nieuw in 2.76.0

- **De looptijd van de Jumbo-aanbiedingen.** Jumbo geeft per promotie een begin en een einde (`start { iso }` en `end { iso }`, met een echt Jumbo-antwoord bevestigd). Bij de aanbiedingen van Jumbo staat nu dezelfde regel bovenaan als bij AH, bijvoorbeeld *Geldig wo 21 okt t/m di 27 okt*, en elke kaart toont *t/m* de einddatum. Een verlopen Jumbo-aanbieding verdwijnt na de einddatum uit de lijst.
- Heeft een product meer promoties (een weekactie en een seizoensactie), dan telt de promotie die nu loopt en het eerst eindigt. Jumbo's weekactie loopt van woensdag tot en met dinsdag, niet van maandag tot en met zondag.
- Kent Jumbo de velden ooit niet meer, dan zoekt de app verder zonder looptijd, in plaats van te stoppen met ophalen.
- **De Jumbo-verkenner** (Beheer, Winkels) vraagt de looptijd altijd mee en probeert geen veldnamen meer die niet bestaan (`startDate`, `endDate`, `validFrom`, `validUntil`).

#### Goed om te weten bij het bijwerken

- Synchroniseer de Jumbo-aanbiedingen één keer opnieuw: een kopie van vóór deze versie heeft nog geen looptijd.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.75.0

*English summary: The AH Bonus now shows the period the offers are valid for at the top of the Offers screen (for example "Valid Mon 26 Oct to Sun 1 Nov"), taken from the start and end dates AH gives per offer. Ongoing "volume discount" offers, which AH gives the end date 31 Dec 2999, no longer show a bogus "until 31 Dec".*

### Nieuw in 2.75.0

- **De looptijd van de bonus staat bovenaan de aanbiedingen.** Boven het aantal aanbiedingen staat nu bijvoorbeeld *Geldig ma 26 okt t/m zo 1 nov*: de looptijd die de meeste aanbiedingen delen. Die komt uit de begin- en einddatum die AH per aanbieding meegeeft (`bonusStartDate` en `bonusEndDate`, met een echt AH-antwoord bevestigd). Geeft een winkel geen datums, dan blijft de regel weg.
- **De begindatum wordt bewaard** (`valid_from` bij de lokale kopie van de aanbiedingen). Een kopie van vóór deze versie krijgt hem bij de volgende synchronisatie; zolang staat er geen periode bovenaan.

### Opgelost in 2.75.0

- **Doorlopende aanbiedingen** ("5% volume voordeel" en dergelijke) hebben bij AH `31-12-2999` als einddatum. Ze stonden daardoor met *t/m 31 dec* op de kaart. Nu hebben ze geen einddatum, en tellen ze niet mee in de looptijd bovenaan.

#### Goed om te weten bij het bijwerken

- Pas de database niet handmatig aan: de nieuwe kolom wordt vanzelf toegevoegd. Een back-up van een eerdere versie laat zich gewoon terugzetten.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.74.0

*English summary: Settings > System has a new Backup and restore card. One click makes a single file with everything in the database, including the links with Mealie, Albert Heijn, Picnic, Jumbo and the AI keys (encrypted with a password you choose) and the whole history of the (bonus) offers, and restoring it brings the app back to a working state, also on a new server. The restore is checked completely before anything changes, is all or nothing, shows its progress and keeps running on the server if you leave the page.*

### Nieuw in 2.74.0

- **Back-up maken en terugzetten in de app (Instellingen, Systeem).** Eén bestand (`mealieshopper-backup-….json`) met alles wat in de database zit: de koppelingen met Mealie, Albert Heijn, Picnic, Jumbo en de AI-sleutels, de kopieën van je lijstjes, bewaarde recepten, bestellingen en kassabonnen, de aanbiedingen van nu **en de hele historie van de (bonus)aanbiedingen**, de beoordelingen van aanbiedingen bij recepten, de profielen en de keuzes bij AI en MCP. Terugzetten, ook op een nieuwe server, geeft een werkende app.
- **De koppelingen gaan versleuteld mee, met een wachtwoord dat je zelf kiest** (scrypt en Fernet). Het wachtwoord wordt nergens bewaard. Zonder het vinkje *Neem de koppelingen mee* bevat het bestand geen koppelingen en is er geen wachtwoord nodig. Op de nieuwe server worden de koppelingen opnieuw versleuteld met de sleutel van die server.
- **Wat inlogt op MealieShopper zit er nooit in:** passkeys, wachtwoorden, tweestapsverificatie, herstelcodes, uitnodigingen, sessies, de toegang van AI-assistenten (MCP) en `secret.key`. Het auditlog, de bewaarde receptafbeeldingen en het webadres van de server blijven buiten de back-up.
- **Alles of niets.** De server controleert het hele bestand (onderdelen, kolommen, waarden, afbeeldingen) en laat zien wat erin zit voordat er iets verandert; daarna vervangt hij de gegevens in één transactie. Een afgekeurd bestand of een fout halverwege laat alles zoals het was. Een bestand van een nieuwere versie wordt geweigerd met de melding dat je eerst moet bijwerken.
- **Je ziet wat er gebeurt.** Maken, downloaden, uploaden, controleren en vervangen hebben elk een naam en, waar te tellen, een getal en een balk; daarna staat er hoe lang elk deel duurde. Het terugzetten is werk van de server: de pagina verlaten of sluiten stopt niets, en het scherm pakt de voortgang weer op.
- Het patroon volgt [`backup-restore.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/shared/guidelines/backup-restore.md) van App-Guidance, met één bewuste afwijking: de koppelingen mogen mee, versleuteld.

#### Goed om te weten bij het bijwerken

- **Een back-up bevat geen inloggegevens van de app.** Zet je terug op een nieuwe installatie, stel dan eerst het inloggen in en zet daarna de back-up terug.
- **Een reverse proxy moet een back-up doorlaten.** Een back-upbestand mag tot 200 MB zijn (in de praktijk enkele megabytes). Sta je een kleinere aanvraag toe, dan geeft de proxy een foutmelding bij het terugzetten.
- **Het scherm Systeem heeft een nieuw script.** Laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.
- Compose- of `.env`-bestanden zijn niet gewijzigd.

## v2.73.1

### Sinds de vorige stabiele versie (v2.72.1)

Deze release brengt `main` van v2.72.1 naar v2.73.1. De tussenliggende versies (2.72.2 tot en met 2.73.0) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.72.1: filling the AH basket from the week menu no longer fails when two recipes need the same product (409 "Grocery items cannot be duplicate"); AH product searches are retried when AH drops the connection, and an open order at AH ("Server in order mode", 412) is explained in plain words; in the check list of the week menu you can search for products that were not found, and for an alternative to a product that was found, under a search term of your own; and passkeys are now checked by the open-source py_webauthn library (existing passkeys keep working).*

- **Het mandje vullen vanuit het weekmenu geeft geen 409 meer (2.72.3).** Hadden twee recepten hetzelfde product nodig (bijvoorbeeld melk), dan weigerde AH het met `409 Grocery items cannot be duplicate`. Regels met hetzelfde product worden nu samengevoegd, met de aantallen opgeteld (maximaal 99).
- **Een verbroken verbinding bij AH is niet meer fataal, en een open bestelling wordt uitgelegd (2.72.4).** Zoeken naar producten probeert een verbroken verbinding tot drie keer opnieuw. Weigert AH de boodschappenlijst met `412 Server in order mode`, dan staat er ergens een bestelling open (bijvoorbeeld op een ander apparaat) en zegt MealieShopper dat je die eerst moet afsluiten.
- **Niet gevonden producten zoek je zelf op, en bij een gevonden product zoek je een alternatief (2.73.0 en 2.73.1).** In de controlelijst van het weekmenu heeft elke regel een zoekveld met een eigen zoekterm (bijvoorbeeld "pak choi" voor "paksoi"). Dat geldt voor Albert Heijn, Picnic en Jumbo.
- **Passkeys worden gecontroleerd door py_webauthn (2.72.2).** De eigen WebAuthn-controle is vervangen door de open-sourcebibliotheek [py_webauthn](https://github.com/duo-labs/py_webauthn). Het gedrag blijft hetzelfde en bestaande passkeys blijven werken.

#### Goed om te weten bij het bijwerken

- **Bouw de container opnieuw:** `requirements.txt` heeft nieuwe afhankelijkheden voor passkeys (`webauthn`, `pyasn1`, `pyasn1_modules`, `pyOpenSSL`; `cbor2` en `cryptography` zijn nieuwer). Haal je de image `:latest` op, dan zit dat erin; bouw je zelf, bouw dan opnieuw.
- **Het weekmenu heeft nieuwe knoppen in de controlelijst.** Ververst een scherm niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).
- **Het mandje vullen lukt niet zolang er een AH-bestelling open staat.** Sluit die eerst af (bijvoorbeeld in de AH-app of op de website van een ander apparaat) en probeer het opnieuw.

### Nieuw in 2.73.1

- **Ook bij een gevonden product zoek je een alternatief (2.73.1).** In de controlelijst van het weekmenu heeft elke regel met producten nu de knop *Zoek een ander product*. Die klapt een zoekveld uit onder de regel, met de zoekterm erin. Zoek je met een eigen term, dan vervangt de lijst met opties van die regel door de nieuwe resultaten (met het aantal verpakkingen opnieuw berekend uit de oorspronkelijke hoeveelheid) en blijft het vinkje aan. Vindt de zoekopdracht niets, dan staat dat bij de regel en houdt de regel zijn huidige producten. De andere regels blijven zoals je ze had ingesteld. Dit geldt voor Albert Heijn, Picnic en Jumbo. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script en de nieuwe stijlen laadt; laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.

## v2.73.0

*English summary: products that could not be found when filling the basket from the week menu can now be searched for one by one in the check list, under a search term of your own (for example "pak choi" for "paksoi"), and added after all.*

- **Niet gevonden producten zoek je zelf op (2.73.0).** In de controlelijst van het weekmenu heeft elke regel die niet gevonden werd nu een zoekveld met de zoekterm erin (aan te passen) en een knop *Zoeken*. Vind je met je eigen term wel producten, dan wordt de regel een gewone regel met keuzelijst, aantal verpakkingen (berekend uit de oorspronkelijke hoeveelheid) en vinkje, en gaat hij mee in het mandje. Vind je niets, dan staat dat bij de regel en kun je een andere term proberen. Een zoekopdracht die door een storing mislukte (zoals een verbroken verbinding) probeer je op dezelfde manier opnieuw. Wat je bij de andere regels hebt gekozen blijft staan. Dit geldt voor Albert Heijn, Picnic en Jumbo, want ze delen de controlelijst.
- **Boven de lijst staat hoeveel producten nog niet gevonden zijn**, en de melding na het vullen noemt alleen wat echt ontbreekt. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script en de nieuwe stijlen laadt. Laad de pagina één keer opnieuw als het scherm niet vanzelf ververst.
- **Test.** Een enkele regel opnieuw zoeken geeft de oorspronkelijke hoeveelheid en eenheid terug en rekent de verpakkingen daarmee uit.

## v2.72.4

*English summary: AH product searches are retried when AH drops the connection, and a basket fill that AH refuses because an order is open ("Server in order mode", 412) now says so in plain words: close the open order (for example on another device) first.*

- **Een verbroken verbinding bij AH is niet meer fataal (2.72.4).** Het weekmenu zoekt elk ingrediënt apart bij AH, en AH verbrak daarbij af en toe de verbinding (`Connection reset by peer`), waarna dat ingrediënt ontbrak. Zoeken naar producten en bonusproducten probeert het nu tot drie keer, met een korte pauze. Een antwoord van AH met een fout (zoals 500) wordt niet herhaald, en het vullen van het mandje zelf ook niet, zodat er niets dubbel kan binnenkomen.
- **Duidelijke melding bij een open bestelling.** Weigert AH de boodschappenlijst met `412 Server in order mode`, dan staat er ergens een bestelling open, bijvoorbeeld in de AH-app of op de website van een ander apparaat. MealieShopper zegt dat nu: sluit die bestelling eerst af en probeer het daarna opnieuw, in plaats van de ruwe melding te tonen.
- **Tests.** Opnieuw proberen na een verbroken verbinding, opgeven na drie pogingen, geen herhaling bij een foutantwoord, en de melding bij 412.

## v2.72.3

*English summary: filling the AH basket from the week menu no longer fails with "Grocery items cannot be duplicate" (409) when two recipes need the same product; those lines are now merged into one with the quantities added up.*

- **Weekmenu in het AH-mandje geeft geen 409 meer (2.72.3).** Hadden twee recepten in het weekmenu hetzelfde product nodig (bijvoorbeeld melk), dan stuurde MealieShopper dat product twee keer in één verzoek mee en weigerde AH het met `409 Grocery items cannot be duplicate`. Regels met hetzelfde AH-product worden nu vóór het versturen samengevoegd tot één regel, met de aantallen opgeteld (maximaal 99, de grens van AH). Dat geldt ook voor het mandje vullen vanuit een AH-lijstje en voor de aanroep van `/api/ah/cart`.
- **Test.** Dubbele producten gaan één keer naar AH, met opgetelde aantallen, ook als het product-ID als tekst binnenkomt.

## v2.72.2

*English summary: passkeys are now checked by the open-source py_webauthn library instead of our own code; behaviour is the same, and passkeys that were already registered keep working.*

- **Passkeys worden gecontroleerd door py_webauthn (2.72.2).** De eigen controle van de aanmaak- en inlogantwoorden (clientgegevens, uitdaging, origin, RP-ID, vlaggen en handtekening) is vervangen door de open-sourcebibliotheek [py_webauthn](https://github.com/duo-labs/py_webauthn), zoals de bouwregel van Flux76 voorschrijft: een standaard implementeer je met een bestaande module. Het gedrag blijft hetzelfde: alleen ES256, gebruikersaanwezigheid verplicht, verificatie niet (zodat wachtwoordmanagers als Enpass blijven werken) en de tellercontrole blijft uit. Bestaande passkeys blijven werken, de opgeslagen sleutels hebben hetzelfde formaat.
- **Nieuwe afhankelijkheden.** `requirements.txt` krijgt `webauthn`, `pyasn1`, `pyasn1_modules` en `pyOpenSSL`, en `cbor2` en `cryptography` gaan naar de versies die py_webauthn vraagt (`cbor2` 6.1.5, `cryptography` 50.0.2). Ze staan ook bij *Over* en in de README's. Bouw de container opnieuw bij het bijwerken.
- **Tests.** Een passkey zonder gebruikersverificatie (zoals Enpass meldt) kan aanmaken en inloggen; een passkey zonder gebruikersaanwezigheid, van een andere origin of voor een andere uitdaging wordt geweigerd. De versie van de statische bestanden is opgehoogd, zodat de browser de nieuwe teksten bij *Over* laadt.

## v2.72.1

### Sinds de vorige stabiele versie (v2.67.2)

Deze release brengt `main` van v2.67.2 naar v2.72.1. De tussenliggende versies (2.68.0 tot en met 2.72.0) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.67.2: optional AI help with your own Claude or ChatGPT API key (review the matches between recipe ingredients and offers, and search your recipes for the products on offer; nothing happens without a key); recipe lines are parsed into quantity, unit and ingredient when you import a recipe into Mealie and when Mealie did not analyze them; putting the week menu in the basket now always shows the check list first; and the build you install (`:latest` or `:beta`) is chosen with `MEALIESHOPPER_TAG` in a `.env` file next to `docker-compose.yml`, with a "beta" badge in the app on a beta build.*

- **Het weekmenu gaat nooit meer ongezien in het mandje (2.72.0 en 2.72.1).** De knop in de planner toont altijd eerst de controlelijst, met per ingrediënt het gekozen product, de andere keuzes, het aantal verpakkingen en een vinkje; pas na het bevestigen gaan de producten in het mandje (Albert Heijn, Picnic en Jumbo). Voorheen werd het mandje meteen gevuld als de app nergens twijfelde. De versie van de statische bestanden is opgehoogd, zodat de browser het nieuwe script laadt.
- **Optionele AI-hulp met je eigen sleutel (2.68.0 en 2.69.0).** Onder *Instellingen > Koppelen* vul je een API-sleutel in van Claude (Anthropic) en/of ChatGPT (OpenAI), of zet je `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. De sleutel wordt versleuteld bewaard en nooit meer getoond. Met een sleutel verschijnt het subtabblad *Recepten* bij Aanbiedingen en krijgt de beheerder de knop *Recepten zoeken met AI*: na een bevestiging met een schatting van het aantal tokens zoekt de AI in je eigen recepten (Mealie, bewaarde AH-recepten, Jumbo-favorieten, Picnic-recepten) bij de producten in de aanbieding. Alleen de tekst van ingrediënten en producten wordt verstuurd, alleen als je op de knop drukt; de AI kan geen producten of regels verzinnen, want alles gaat op nummer. Zonder sleutel verandert er niets en gaat er niets naar een AI-dienst.
- **Eenheden en ingrediënten bij het importeren in Mealie (2.69.1 en 2.70.0).** Na een import splitst MealieShopper regels als `200 ml melk` in aantal, eenheid en ingrediënt en koppelt die aan de eenheden en ingrediënten van Mealie (ontbrekende worden aangemaakt), zodat het recept schaalt en de boodschappenlijst optelt. Dat geldt voor Allerhande, Jumbo, Picnic, een gewone receptpagina en de MCP-tool `import_recipe_to_mealie`. Regels die Mealie niet had geanalyseerd worden ook bij het ophalen van het recept herkend, zodat het mandje niet meer "niet gevonden" meldt. Mislukt het analyseren, dan blijft het recept zoals Mealie het importeerde.
- **De build kies je met `MEALIESHOPPER_TAG` (2.71.0).** `docker-compose.yml` haalt `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` op en geeft dezelfde waarde door aan de app, die op een beta-build een **beta**-badge toont (ook bij Over; `GET /api/about` geeft `channel`). Er is een nieuw voorbeeldbestand `.env.example`. Zonder de variabele blijft het `latest`.
- **Nieuwe routes (alleen beheerder):** `GET` en `PUT /api/offers/ai`, `POST /api/offers/ai/test`, `POST /api/offers/ai/review`, `GET /api/offers/ai/search/plan`, `POST /api/offers/ai/search` en `GET /api/offers/ai/search/status`. De MCP-tools zijn niet veranderd.

#### Goed om te weten bij het bijwerken

- **Het weekmenu werkt anders:** je ziet nu altijd eerst de controlelijst voordat het mandje wordt gevuld. Ververst een scherm niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).
- **`docker-compose.yml` is veranderd:** de image-tag komt nu uit `MEALIESHOPPER_TAG` (standaard `latest`). Gebruik je een eigen kopie van het compose-bestand, neem die regel dan over of laat alles zoals het was; er verandert dan niets.
- **De AI-functies zijn optioneel en staan uit zonder sleutel.** De aanroepen naar de AI-aanbieders komen niet in het auditlog en de sleutel komt nergens in een log of API-antwoord. De kwaliteit van de AI-koppelingen en de grootte van de aanvragen bij een groot aanbod zijn nog niet op echte gegevens gecontroleerd: begin met een kleine ronde.
- De database wordt zo nodig vanzelf aangevuld (nieuwe tabellen `offer_verdicts`, `ai_recipe_matches` en `ai_recipe_runs`); je hoeft niets te migreren.

Pull requests sinds v2.67.2: #118 tot en met #123.

*English summary: Fix for 2.72.0. The static-files version was not raised, so browsers and the installed app kept running the old script, which still filled the basket straight away when the app had no doubts. The version is raised now, so every browser loads the script that always shows the check list first.*

### Opgelost in 2.72.1

- **Het weekmenu ging in sommige situaties nog steeds direct in het mandje.** In 2.72.0 hebben we de controlelijst altijd getoond, maar de versie van de statische bestanden (`MEALIESHOPPER_ASSET_VERSION`) bleef gelijk. Daardoor bleef een browser of de geïnstalleerde app het oude script gebruiken, dat het mandje meteen vulde zodra de app nergens twijfelde. De versie is opgehoogd (20261023-42), zodat het nieuwe script wordt geladen en de controlelijst altijd verschijnt.

### Goed om te weten

- Ververst een scherm nog niet vanzelf, laad de pagina dan één keer opnieuw (in de geïnstalleerde app: sluiten en weer openen).

## v2.72.0

*English summary: Putting the week menu in the basket now always shows the check list first, with the product choices next to every line, even when the app has no doubts. You can untick lines and change the product or number of packs; only after you confirm are the products put in your basket (Albert Heijn, Picnic and Jumbo).*

### Gewijzigd in 2.72.0

- **Het weekmenu gaat nooit meer ongezien in het mandje.** Voorheen verscheen de controlelijst alleen als de app ergens twijfelde; anders werd het mandje meteen gevuld. Nu toont de knop in de planner altijd eerst de lijst: per ingrediënt het gekozen product met de andere keuzes ernaast, het aantal verpakkingen en een vinkje. Haal een vinkje weg als een regel toch niet klopt, of kies een ander product of aantal. Pas met `Voeg … toe aan mandje` gaan de producten erin. Geldt voor Albert Heijn, Picnic en Jumbo.
- Regels waar de app niet zeker van is blijven gemarkeerd; de uitleg boven de lijst past zich aan als er niets gemarkeerd is.

## v2.71.0

*English summary: The build you install (`:latest` or `:beta`) is now set in one place: `MEALIESHOPPER_TAG` in a `.env` file next to `docker-compose.yml`. The compose file pulls `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` and passes the same value to the app, which shows a "beta" badge next to the wordmark (and under About) on a beta build. Without the variable nothing changes: `latest`, no badge. New `.env.example`.*

### Nieuw in 2.71.0

- **De build (`:latest` of `:beta`) stel je in via `.env`.** `docker-compose.yml` haalt nu `ghcr.io/helmerznl/mealieshopper:${MEALIESHOPPER_TAG:-latest}` op en geeft dezelfde `MEALIESHOPPER_TAG` door aan de container. Zet `MEALIESHOPPER_TAG=beta` in `.env` naast de compose-file (voorbeeld: `.env.example`), draai `docker compose pull && docker compose up -d` en je draait de beta-build. Zonder de variabele verandert er niets: `latest`.
- **Een beta-badge in de app.** Bij een beta-build (de tag bevat `beta`, dus ook `2.71.0-beta`) staat er een **beta**-badge naast het woordmerk in de kop, en bij Over staat *beta* achter de versie. `GET /api/about` geeft ook `channel` (`stable` of `beta`).

### Goed om te weten

- Compose leest `.env` zelf voor de image-tag; `.env.unraid` is alleen voor de app. Staat `MEALIESHOPPER_TAG` alleen in `.env.unraid`, dan wint de waarde uit `.env` (of `latest`), zodat image en badge altijd bij elkaar passen. Wil je `.env.unraid` toch gebruiken: `docker compose --env-file .env.unraid up -d`.
- Draai je de container met `docker run`, zet dan zelf `-e MEALIESHOPPER_TAG=beta` bij de beta-image om de badge te tonen.

## v2.70.0

*English summary: When a recipe is imported into Mealie, its ingredient lines are now parsed into quantity, unit and ingredient. Mealie stores imported lines as plain text ("200 ml milk"); MealieShopper now splits them and links them to Mealie's own units and foods (missing ones are created), so the recipe scales, the shopping list adds up and the display is right. Also includes the 2.69.1 fix for lines Mealie did not analyze.*

### Nieuw in 2.70.0

- **Bij het importeren in Mealie worden de eenheden en ingrediënten goed verwerkt.** Mealie bewaart een geïmporteerde regel als losse tekst (`200 ml melk`, `1 el maïzena`) in de notitie. Direct na de import haalt MealieShopper het aantal, de eenheid en het ingrediënt eruit en koppelt die aan de eenheden en ingrediënten van Mealie. Zo kent Mealie ze zelf: het recept kan schalen, de boodschappenlijst telt hoeveelheden op en de weergave klopt.
- **Eenheden worden herkend onder hun eigen naam.** `gr` en `gram` zijn de eenheid *gram* (`g`), `el` is *eetlepel*, enzovoort; staat de eenheid al in Mealie (op naam, meervoud, afkorting of alias), dan wordt die gebruikt. Een eenheid of ingrediënt dat Mealie nog niet kent, wordt aangemaakt (zoals ook de ingrediëntenparser van Mealie zelf doet).
- **Geldt voor elke manier van importeren:** Allerhande, Jumbo, Picnic, een gewone receptpagina en de MCP-tool `import_recipe_to_mealie`. Regels die Mealie al zelf analyseerde, en regels zonder aantal of ingrediënt (`peper naar smaak`), blijven zoals ze zijn.
- **De import zelf kan hierdoor niet mislukken.** Lukt het analyseren niet (Mealie onbereikbaar, geen rechten), dan blijft het recept zoals Mealie het importeerde en staat er een waarschuwing in het logboek.

## v2.69.1

*English summary: Recipe lines that Mealie did not analyze (quantity 1 with the text "200 ml milk" in the note, shown as "1 200 ml milk") are now split into quantity, unit and ingredient when the recipe is fetched. They show as "200 ml milk" and the store search uses "milk" instead of the whole text, so these lines are found in the cart.*

### Opgelost in 2.69.1

- **Ingrediënten die Mealie niet analyseerde worden nu goed herkend.** Een recept waarvan Mealie de ingrediënten niet had geanalyseerd, bewaart een regel als `200 ml melk` in de notitie, met aantal 1 en zonder eenheid of ingrediënt. Het weekmenu toonde dat als *1 200 ml melk* en het boodschappenmandje zocht in de winkel op die hele tekst, dus "9 niet gevonden". MealieShopper haalt nu bij het ophalen van het recept het aantal, de eenheid en het ingrediënt uit zo'n regel (`200`, `ml`, `melk`), zodat hij zich gedraagt als een regel die Mealie zelf analyseerde: *200 ml melk*, zoeken op *melk* en het aantal verpakkingen op basis van 200 ml.
- **Wat er wordt herkend:** hele en decimale getallen (`1,5`), breuken (`1/2`, `1 1/2`, `½`), bereiken (`2-3 el`, het hoogste aantal telt), `2x`, een eenheid zonder aantal (`snufje zout` is 1 snufje), en een toevoeging na een komma (`1 ui, fijngesneden`) of tussen haakjes.
- **Wat ongemoeid blijft:** regels die Mealie al analyseerde (met een ingrediënt of een eenheid), regels met een eigen aantal (`2` + `400 g tomaten`) en regels zonder aantal of ingrediënt (`peper naar smaak`).
- Dit geldt overal waar MealieShopper Mealie-recepten leest: het weekmenu, de boodschappenmandjes, de receptenindex voor de aanbiedingen en de MCP-tools.

## v2.69.0

*English summary: The AI now searches recipes for the products on offer, instead of only approving existing matches. The "Review with AI" button became "Search recipes with AI": after a confirmation with a token estimate, a round of up to 120 recipes from all your sources (Mealie, saved AH recipes, Jumbo favorites, Picnic recipes) is sent to Claude or ChatGPT together with the numbered food offers of the chosen store; the AI says which ingredient line fits which product number. Numbers that do not exist or are used twice are dropped, so nothing can be made up. The round runs in the background with visible progress (also after a reload). For the recipes the AI searched, its matches replace those of MealieShopper (check mark "Found by AI"); other recipes keep ours. A recipe is searched again only when the offer or its ingredients changed. Only product and ingredient text is sent, only when you press the button. No live search in the stores' recipe catalogues yet.*

### Aangepast in 2.69.0

- **De AI zoekt nu zelf recepten bij de producten in de aanbieding.** De knop *Beoordelen met AI* heet nu *Recepten zoeken met AI*. De AI krijgt het genummerde aanbod van de gekozen winkel (alleen eten) en een groep van 40 recepten met genummerde ingrediëntregels, en zegt per recept welke regel met welk productnummer gekoppeld kan worden. Producten en regels gaan op nummer: onbekende of dubbele nummers en basisingrediënten worden weggelaten, dus er kan niets verzonnen worden.
- **Bronnen:** alle recepten die MealieShopper al heeft: je Mealie-recepten, bewaarde AH-recepten, Jumbo-favorieten en Picnic-recepten. De AI zoekt niet live in het hele receptenaanbod van de winkels.
- **De AI vervangt per recept onze koppelingen.** Een recept dat de AI doorzocht toont haar koppelingen (met het vinkje *Gevonden door AI*), ook als dat er geen zijn: dan valt het recept weg. Een recept dat nog niet doorzocht is, houdt de koppelingen van MealieShopper. Producten die uit het aanbod zijn verdwenen en koppelingen die je met de vlag meldde, vallen weg.
- **Kosten onder controle.** Vooraf zie je een schatting van het aantal tokens en het aantal recepten (maximaal 120 per ronde, 40 per aanvraag; bij Claude staat het aanbod in een blok dat de aanbieder kan cachen); na afloop het gebruikte aantal. Een recept wordt pas opnieuw doorzocht als het aanbod van de winkel of de ingrediënten van het recept veranderd zijn, dus een volgende ronde kost alleen wat nieuw is.
- **Op de achtergrond.** Een ronde loopt in een eigen draad, één tegelijk, met een voortgangsregel (*12 van 120 recepten doorzocht...*) die ook na het herladen van de pagina weer verschijnt. Een fout (sleutel ongeldig, tegoed op) bewaart wat al klaar was; de volgende ronde gaat verder.
- **Nieuwe routes (alleen beheerder):** `GET /api/offers/ai/search/plan`, `POST /api/offers/ai/search` en `GET /api/offers/ai/search/status`; `GET /api/offers/ai` geeft ook de stand van een lopende ronde. Nieuwe tabellen `ai_recipe_matches` en `ai_recipe_runs`.

### Goed om te weten

- Het beoordelen van bestaande koppelingen (`POST /api/offers/ai/review`, de oordelen en hun vinkjes) bestaat nog als API, maar heeft geen knop meer.
- De echte aanbieders zijn niet aangeroepen tijdens de ontwikkeling: de kwaliteit van de koppelingen, de grootte van de aanvragen bij een groot aanbod (bijvoorbeeld Jumbo) en het cachen bij Claude zijn het eerste om op echte gegevens te controleren. Begin met een kleine ronde (de bevestiging toont de schatting).
- De AI beoordeelt alleen recepten uit de index; eerst *Recepten inlezen* als je nieuwe recepten hebt.

## v2.68.0

*English summary: Optional AI review of the matches between recipe ingredients and products on offer, with your own API key from Claude (Anthropic) and/or ChatGPT (OpenAI). Enter the key under Settings > Link (administrator only; stored encrypted, never shown again) or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; choose the provider and model and test the key. With a key, the Recipes tab under Offers appears and the administrator gets a "Review with AI" button: each unique ingredient + product pair is sent once (only that text, nothing else), the verdict is kept, a rejected match is hidden like a reported one (source `ai`) and an approved one gets a check mark. It runs only when you press the button, asks for confirmation, and shows the tokens used. Without a key nothing changes.*

### Nieuw in 2.68.0

- **Een Claude- of ChatGPT-sleutel om de koppelingen te laten beoordelen (optioneel).** Onder *Instellingen, Koppelen, AI-beoordeling van koppelingen* vul je een API-sleutel in van Claude (Anthropic) en/of ChatGPT (OpenAI), kies je welke aanbieder gebruikt wordt (of *Automatisch*) en pas je zo nodig het model aan (standaard `claude-haiku-5-5` en `gpt-4o-mini`). `Testen` controleert sleutel en model met een kleine aanroep. De sleutel wordt versleuteld bewaard en nooit meer getoond; als startwaarde werken ook `ANTHROPIC_API_KEY` en `OPENAI_API_KEY`.
- **Beoordelen met AI.** Met een sleutel verschijnt het subtab *Recepten* bij Aanbiedingen (ook zonder `MEALIESHOPPER_OFFERS_RECIPES`), met voor de beheerder de knop *Beoordelen met AI*. Na een bevestiging worden de nog niet beoordeelde koppelingen beoordeeld, tot 200 per keer, per 40 in een aanvraag. Elk uniek paar (hoofdwoord van het ingrediënt + product, per winkel) wordt één keer beoordeeld; het oordeel wordt bewaard (tabel `offer_verdicts`), ook als je een melding later ongedaan maakt.
- **Het oordeel werkt zoals de vlag.** Een afgekeurde koppeling verdwijnt (en het volgende passende product neemt de plaats in); de melding staat bij `list_offer_mismatches` met bron `ai` en de reden. Een goedgekeurde koppeling krijgt een vinkje (MDI *check-circle-outline*), en boven de kaarten staat hoeveel koppelingen al beoordeeld zijn. Een melding van jou of van een assistent gaat voor.
- **Wat er verstuurd wordt.** Alleen de tekst van het ingrediënt en van het product (naam, merk, inhoud, categorie), geen recepttitels, namen of adressen, en alleen als je op de knop drukt. De AI krijgt de opdracht de teksten als gegevens te behandelen en niet te volgen wat erin staat; haar antwoord is beperkt tot een vast schema. Na afloop staat het aantal gebruikte tokens in de melding; een prijs in euro's wordt niet getoond (prijzen veranderen).
- **Nieuwe routes (alleen de beheerder):** `GET` en `PUT /api/offers/ai`, `POST /api/offers/ai/test` en `POST /api/offers/ai/review`.

### Goed om te weten

- Zonder sleutel verandert er niets: er gaat niets naar een AI-dienst en het tabblad *Recepten* blijft verborgen (tenzij `MEALIESHOPPER_OFFERS_RECIPES=true`).
- De AI beoordeelt wat het zoeken al vindt; ze zoekt zelf geen nieuwe koppelingen.
- De aanroepen naar de AI-aanbieders komen niet in het auditlog en de sleutel komt nergens in een log, foutmelding of API-antwoord. Is de sleutel ongeldig, staat het model niet meer bij de aanbieder of is het tegoed op, dan staat dat leesbaar in de melding; wat al beoordeeld was, blijft bewaard.

## v2.67.2

### Sinds de vorige stabiele versie (v2.53.0)

Deze release brengt `main` van v2.53.0 naar v2.67.2. De tussenliggende versies (2.53.1 tot en met 2.67.1) waren alleen als pre-release (`-beta`) beschikbaar; dit is wat er in totaal verandert. De volledige tekst per versie staat verderop in dit bestand.

*English summary of everything since v2.53.0: the new Offers tab (offers of Albert Heijn, Picnic and Jumbo, in categories, refreshed daily in the background, with a permanent history, the conditions and end date of each offer, and a cache so switching store is quick); recipe suggestions based on the offers of the chosen store (hidden by default in this release: set `MEALIESHOPPER_OFFERS_RECIPES=true` to show them), with clickable linked products, English ingredients translated for matching, a flag to report wrong matches and three new MCP tools so ChatGPT or Claude can review the matches; a reorganised interface (Settings > Link on one page, an avatar menu with Profile and Settings, a vertical menu on large screens, a week menu that loads by itself, the same subtab order for every store); links to Mealie recipes that work again; and Settings > System with a factory reset.*

- **Aanbiedingen (2.53.0 tot en met 2.67.1).** Een nieuw tabblad **Aanbiedingen** met de aanbiedingen van Albert Heijn, Picnic en Jumbo, per categorie, met oude en nieuwe prijs, de voorwaarden (*bij 2 stuks*) en de einddatum. Een achtergrondtaak ververst ze elke dag (standaard rond 5 uur) en de beheerder synchroniseert ze meteen onder *Instellingen > Koppelen*. Elke synchronisatie komt ook in een blijvende geschiedenis (`GET /api/offers/history`). Bij het wisselen van winkel toont de pagina een laadicoon en onthoudt ze de aanbiedingen; `GET /api/offers` stuurt een `ETag` mee.
- **Receptvoorstellen (verborgen in deze release).** Recepten uit Mealie, Jumbo, Picnic en AH worden vergeleken met de aanbiedingen van de gekozen winkel, met de meeste ingrediënten in de aanbieding bovenaan, een filter op soort gerecht en per recept wat er niet in de aanbieding is. Gekoppelde producten zijn aan te klikken (venster met plaatje, prijs en voorwaarden), Engelse ingrediënten worden vertaald voor het vergelijken, hetzelfde recept uit twee bronnen is één kaart en de koppelingen zijn strenger (basisingrediënten, bereide producten, kleur, keuken en vetgehalte). Omdat de koppelingen nog niet goed genoeg zijn, staat het subtabblad *Recepten* sinds v2.67.0 standaard uit; met `MEALIESHOPPER_OFFERS_RECIPES=true` komt het terug.
- **Koppelingen beoordelen (2.66.0 en 2.67.0).** Een onjuiste koppeling meld je met een kleine vlag of de knop *Klopt niet*; de melding blijft bewaard (tabel `offer_rejections`) en geldt voor het hoofdwoord in die winkel. ChatGPT en Claude kunnen de koppelingen via de MCP-server beoordelen met de nieuwe tools `review_offer_matches`, `report_offer_mismatch` en `list_offer_mismatches`; er draait geen AI in MealieShopper zelf.
- **Nieuwe indeling.** *Instellingen > Koppelen* is één pagina met de status van Mealie en de winkels bovenaan; een avatar-menu met Profiel, Instellingen en Over vervangt het tabblad Beheer; op een groot scherm staat het hoofdmenu links en is het inklapbaar; het weekmenu laadt vanzelf met een cache van 30 minuten; de subtabs onder *Winkels* staan bij elke winkel in dezelfde volgorde (Recepten eerst) en *AH Recepten* heet nu *Recepten*.
- **Instellingen > Systeem.** Het tabblad *Updates* heet nu *Systeem* en bevat de updatecheck en de beheerdersfunctie *Fabrieksinstellingen* (v2.63.0 verderop).
- **Opgelost.** Links naar Mealie-recepten gebruiken weer het adres dat Mealie zelf gebruikt (`/g/<groep>/r/<recept>`), ook na het importeren van een recept en in de MCP-tools.
- **Nieuwe routes** (alleen beheerder, tenzij anders vermeld): `GET /api/offers`, `POST /api/offers/sync`, `GET /api/offers/recipes`, `POST /api/offers/recipes/sync`, `GET /api/offers/status`, `POST /api/offers/sync-all`, `GET /api/offers/history`, `POST` en `DELETE /api/offers/rejections`, `GET /api/system/factory-reset`, `POST /api/system/factory-reset/options` en `POST /api/system/factory-reset`. **Nieuwe MCP-tools:** `review_offer_matches`, `report_offer_mismatch` en `list_offer_mismatches`.
- **Verwijderd.** De knop *Aanbiedingen ophalen* op het tabblad Aanbiedingen (de aanbiedingen komen uit de lokale kopie), het aparte tabje *Mealie* (nu onderdeel van *Koppelen*) en het tabblad *Beheer* (nu in het avatar-menu).

#### Goed om te weten bij het bijwerken

- **Nieuwe, optionele instellingen:** `MEALIESHOPPER_OFFERS_SYNC_HOUR` (uur van de dagelijkse run, standaard 5), `MEALIESHOPPER_OFFERS_SCHEDULE=false` om de dagelijkse taak uit te zetten en `MEALIESHOPPER_OFFERS_RECIPES=true` om de receptvoorstellen onder Aanbiedingen weer te tonen. Zonder aanpassing werkt alles zoals voorheen, met de receptvoorstellen verborgen.
- **Zet je de receptvoorstellen aan, lees je recepten dan één keer opnieuw in** (*Aanbiedingen > Recepten > Recepten inlezen*): de soort gerecht wordt daarbij bewaard. Tot dan staan alle recepten onder *overig*.
- De database wordt zo nodig vanzelf aangevuld (onder meer de kolom *categorie*, de geschiedenis van de aanbiedingen en de tabel met gemelde koppelingen); je hoeft niets te migreren.
- De aanbiedingen van Picnic en Jumbo komen uit onofficiële routes en kunnen veranderen. Geeft een winkel niets terug, dan staat dat als melding bij *Koppelen* en bij *Aanbiedingen*.
- De screenshots in `docs/screenshots/` kunnen nog een oudere indeling tonen.

Pull requests sinds v2.53.0: #89 tot en met #115.

*English summary: The subtabs under Stores are now in the same order for every store: Recipes, Lists, Orders for Albert Heijn and Jumbo, and Recipes, Favorites, Orders for Picnic. Jumbo showed Lists, Orders, Recipes before. The Albert Heijn tab "AH Recipes" is now just "Recipes", and switching store always opens the Recipes tab of that store (before, Albert Heijn and Jumbo started at Lists).*

### Aangepast in 2.67.2

- **De subtabs onder Winkels staan bij elke winkel in dezelfde volgorde.** Albert Heijn: *Recepten, Lijstjes, Bestellingen*. Jumbo: *Recepten, Lijstjes, Bestellingen* (Recepten stond achteraan). Picnic: *Recepten, Favorieten, Bestellingen*.
- **Het tabblad *AH Recepten* heet nu *Recepten*.** De winkelkeuze ernaast laat al zien dat het om Albert Heijn gaat. De melding bij het indexeren van recepten noemt nu *Winkels > Albert Heijn > Recepten*.
- **Wisselen van winkel opent altijd het tabblad *Recepten* van die winkel.** Voorheen begonnen Albert Heijn en Jumbo bij *Lijstjes*, en bleef een open tabblad staan als het bij de nieuwe winkel bestond. Lijstjes en bestellingen laden voor de nieuwe winkel zodra je ze opent.

## v2.67.1

*English summary: When you switch store under Offers, the list of the previous store now disappears at once and a spinning icon (Material Design Icons "loading") with "Loading the offers of {store}…" shows until the new offers are in. Before, the old list stayed on screen under the new store name, so it looked as if nothing happened. A slow answer from a store you already switched away from no longer overwrites the list. With reduced motion set in the system the icon stands still and the text stays. The offers are also cached now: a store you already saw shows at once when you switch back (and is checked in the background), and the browser only gets the list again when it changed (ETag, 304).*

### Opgelost in 2.67.1

- **Bij het wisselen van winkel was niet te zien dat er geladen werd.** De lijst van de vorige winkel bleef staan, onder de naam van de nieuwe winkel, tot de nieuwe aanbiedingen binnen waren. Nu verdwijnt die lijst meteen en staat er een draaiend icoon (MDI *loading*) met *Aanbiedingen van Jumbo laden…*; daarna komen de aanbiedingen. Dat geldt ook bij het openen van het tabblad en bij een taalwissel.
- **Een traag antwoord van de vorige winkel overschrijft de lijst niet meer.** Wissel je snel twee keer, dan telt alleen de laatste winkel. Een fout haalt het icoon weg en toont de foutmelding.
- **Minder beweging.** Staat in je systeem *beweging verminderen* aan, dan draait het icoon niet; de tekst blijft staan.

### Nieuw in 2.67.1

- **De aanbiedingen worden gecachet.** Wissel je terug naar een winkel die je al zag, dan staat de lijst er meteen (zonder icoon) en controleert de pagina op de achtergrond of er iets veranderd is; alleen bij een andere lijst wordt het scherm vernieuwd, dus je plek en je *Meer* blijven staan.
- **De browser vraagt alleen of er iets veranderd is.** `GET /api/offers` stuurt een `ETag` mee (`Cache-Control: private, no-cache`). Is de lijst niet veranderd, dan volgt een `304` zonder de lijst te laden of te versturen. De ETag verandert bij een nieuwe synchronisatie van die winkel, op een nieuwe dag (aanbiedingen lopen af) en bij een nieuwe versie van de app.

### Goed om te weten

- De server zelf is niet traag: 3000 aanbiedingen laden kost ± 30 ms, dus de database cachen heeft weinig zin. De wachttijd zat in het ophalen en tekenen van de lijst, en tot 2.67.0 ook in de receptvoorstellen die bij elke winkelwissel mee werden berekend; die staan sinds 2.67.0 uit (`MEALIESHOPPER_OFFERS_RECIPES`).
- De cache in de pagina geldt tot je de pagina herlaadt; daarna zorgt de ETag ervoor dat het ophalen klein blijft.

## v2.67.0

*English summary: The recipe suggestions under Offers are hidden for now (set `MEALIESHOPPER_OFFERS_RECIPES=true` to show them again), because the matches are not good enough yet. ChatGPT and Claude can now review the matches through the MCP server: `review_offer_matches` lists the unique ingredient + product pairs of a store, `report_offer_mismatch` reports a wrong one (with a reason, undoable) and `list_offer_mismatches` shows the reports. Reports from the assistant and from the flag in the app share one list and one effect: the match is hidden and the next fitting product takes its place. No AI runs inside MealieShopper itself: your own assistant does the judging, with your own subscription.*

### Aangepast in 2.67.0

- **De receptvoorstellen onder Aanbiedingen zijn verborgen.** Het subtabblad *Recepten* staat standaard uit, zolang de koppelingen niet goed genoeg zijn. Met `MEALIESHOPPER_OFFERS_RECIPES=true` komt het terug. De aanbiedingen zelf en de API (`/api/offers/recipes`) blijven werken.

### Nieuw in 2.67.0

- **ChatGPT en Claude beoordelen de koppelingen, via de MCP-server.** Drie nieuwe tools: `review_offer_matches` (de unieke paren ingrediënt + product van een winkel, het vaakst gebruikte eerst, met het aantal recepten en voorbeelden; `limit` en `offset` om verder te lezen), `report_offer_mismatch` (een paar als onjuist melden, met een korte reden; `undo` haalt de melding weg) en `list_offer_mismatches` (de gemelde koppelingen, met wie meldde en waarom). Vraag het bijvoorbeeld zo: *"Beoordeel met MealieShopper de koppelingen van Jumbo en meld de onjuiste."*
- **Eén meldlijst.** Meldingen van de assistent en van de vlag in de app (v2.66.0) staan in dezelfde tabel en hebben hetzelfde effect: de koppeling verdwijnt, ook bij andere recepten met hetzelfde hoofdwoord in die winkel, en het volgende passende product neemt de plaats in. De tabel heeft er twee kolommen bij (`reason`, `source`); meldingen van 2.66.0 blijven werken.
- **Geen AI in MealieShopper zelf.** Er gaat niets automatisch naar een AI-dienst en er zijn geen API-sleutels nodig: je assistent doet het oordelen, met je eigen abonnement, en alleen als jij erom vraagt.

### Goed om te weten

- `report_offer_mismatch` is de enige schrijftool voor aanbiedingen; ze verwijdert niets en is ongedaan te maken. Je kunt haar, net als elke andere tool, uitzetten onder Profiel, AI-apps.
- De kleine vlag in de app is verborgen samen met het tabblad; dat de melding daar beter in beeld moet komen, wordt opgepakt als het tabblad terugkomt.

## v2.66.0

*English summary: A wrong match between a recipe ingredient and an offer can now be reported with one click: a small flag (the Material Design Icons "flag-outline") next to every matched product, and a "Not a match" button in the product window. The match does not come back, also not in other recipes with the same main ingredient in that store, and the next best product takes its place. You can undo it right after reporting, and a report survives the daily sync. Still no AI involved.*

### Nieuw in 2.66.0

- **Een onjuiste koppeling melden.** Naast elk gekoppeld product onder een receptvoorstel staat een kleine vlag (het MDI-icoon *flag-outline*); in het productvenster staat dezelfde melding als knop *Klopt niet*. Daarna staat er een melding met *Ongedaan maken*, en de koppeling is weg. Heeft de winkel een ander product dat wel past, dan komt dat ervoor in de plaats.
- **Een melding geldt voor het hoofdwoord, in die winkel.** *Kipfilet → AH Kipfilet met saus* melden verbergt die koppeling ook in andere recepten met kipfilet. Bij een andere winkel is het een ander product en blijft het staan. Een Engelse regel (*chicken breast*) wordt gemeld onder het Nederlandse hoofdwoord, net zoals hij wordt gekoppeld.
- **Meldingen blijven bewaard.** Ze staan in een eigen tabel (`offer_rejections`) en overleven de dagelijkse synchronisatie en het opnieuw inlezen van de recepten. Je kunt zo ook zien welke koppelingen niet kloppen, om de regels beter te maken.
- **Nieuwe route:** `POST /api/offers/rejections` meldt een koppeling (`store`, `ingredient`, `offerId`) en `DELETE` met dezelfde velden trekt de melding in.

## v2.65.2

*English summary: The same recipe from two sources (for example an AH recipe you also have in Mealie) is now one card, with "also in Albert Heijn" next to it; the recipe with the best coverage stays, and with equal coverage your own recipe from Mealie. An ingredient with a colour, cuisine or fat content (red bell pepper, Provençal stir-fry vegetables, semi-skimmed milk) no longer matches an offer that names a different one (yellow, Chinese, whole milk).*

### Opgelost in 2.65.2

- **Hetzelfde recept uit twee bronnen is één kaart.** *Supersnelle spaghetti met runderballetjes* stond twee keer in de lijst, één keer uit Mealie en één keer uit Albert Heijn. Recepten met dezelfde titel (hoofdletters, accenten en leestekens tellen niet mee) uit verschillende bronnen worden nu samengevoegd. Het recept met de beste dekking blijft staan, bij gelijke dekking het eigen recept uit Mealie, en op de kaart staat *ook in Albert Heijn*. De aantallen bij *Alle* en bij de soorten gerecht tellen het recept één keer. Twee recepten met dezelfde titel uit dezelfde bron blijven allebei staan: dat zijn twee recepten.
- **Een andere kleur, keuken of vetgehalte is een ander product.** *400 g Provençaalse wokgroente* hoorde bij *AH Wokgroente Chinees*. Noemen het ingrediënt en de aanbieding allebei een kleur (rood, geel, groen, oranje, wit, zwart, bruin, paars, blauw), een keuken of streek (Chinees, Provençaals, Italiaans, Thais, Spaans, …) of een vetgehalte (mager, halfvol, vol) en zijn die verschillend, dan is het geen match: *rode paprika* past niet bij *Paprika geel*, *halfvolle melk* niet bij *Volle melk*. Noemt maar één kant zoiets (*wokgroente*, *Wokgroente Chinees*) of noemt de aanbieding meerdere (*Paprika rood geel groen*), dan blijft het een match. *Rode* en *rood*, *witte* en *wit* en *Chinese* en *Chinees* gelden als hetzelfde woord.

### Goed om te weten

- De kaart toont de bron van het recept dat blijft staan; *ook in …* noemt de bronnen van de samengevoegde recepten.
- De API en de MCP-tools zijn niet veranderd, op het extra veld `alsoIn` bij elk recept in `GET /api/offers/recipes` na.

## v2.65.1

*English summary: Links to Mealie recipes now use the address Mealie itself uses, `/g/<group>/r/<recipe>`, instead of `/recipe/<recipe>` (which gave an error page). This also fixes the link after importing a recipe and the links of the MCP tools, and recipes that were read in before are corrected without reading them again. Six more wrong links between ingredients and offers are gone: a vegetable burger for broccoli, a pie with beef stew for onions, a cream cheese dip for cream cheese, tortelloni with ricotta for ricotta, "spaghetti all'uovo" matching "tagliatelle all'uovo", and a noodle meal for "vegetables".*

### Opgelost in 2.65.1

- **De links naar Mealie-recepten klopten niet.** De knop *Bekijken* bij een receptvoorstel opende `/recipe/<recept>`, terwijl Mealie `/g/<groep>/r/<recept>` gebruikt (bijvoorbeeld `/g/zb/r/witlof-met-ham-en-kaas-uit-de-oven`). MealieShopper vraagt de groep (`groupSlug`) één keer aan Mealie (`/api/users/self`, met je eigen token), onthoudt die een uur en bouwt daarmee het adres op. Bij een oudere Mealie die geen groep geeft, of als Mealie even niet antwoordt, blijft het oude adres staan; na een mislukte poging probeert MealieShopper het na een minuut opnieuw en blokkeert het niets.
  - Dit geldt ook voor de link na het **importeren van een recept** (AH, Picnic, Jumbo, een adres) en voor de **MCP-tools** (`get_week_menu`, `import_recipe_to_mealie`).
  - Recepten die al in de index staan, hoeven niet opnieuw ingelezen: het adres wordt bij het tonen opnieuw opgebouwd uit het bewaarde adres.
- **Zes verkeerde koppelingen uit de receptvoorstellen bij AH**:
  - *500 g broccoli* hoorde bij *AH Terra Groenteburger broccoli paprika* en *2 middelgrote uien* bij *AH Pie runderstoof ui*. Een aanbieding in een andere rauwe soort (vlees) dan het ingrediënt (groente) is een samengesteld product waar het ingrediënt maar een deel van is; die telt niet meer.
  - *150 g koelverse roomkaas met bieslook* hoorde bij *AH Mascarpone roomkaas chili dip*. Een *dip*, *burger*, *nugget* of *schotel* is een bereid product en telt alleen als het ingrediënt dat zelf vraagt.
  - *125 g ricotta* hoorde bij *AH Tortelloni verdi ricotta spinaci*. Gevulde pasta (tortelloni, tortellini, ravioli, cannelloni, gnocchi) en pastavormen (tagliatelle, fusilli, …) zijn nu een maaltijd, ook als er ricotta in zit; ricotta, mascarpone, burrata, pecorino, gorgonzola en camembert zijn kaas.
  - *250 g verse spaghetti all'uovo* hoorde bij *AH Tagliatelle all' uovo*: *uovo* was het hoofdwoord. Een Italiaanse toevoeging (*all'uovo*) telt niet meer mee, dus het hoofdwoord is *spaghetti*.
  - *Verspakket plaattaart groente* hoorde bij *Knorr Good noodles groente*. *Groente* en *fruit* zijn nu net als *saus* en *kaas* een soort: de andere woorden van het ingrediënt moeten er ook in staan.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd, op het adres van een Mealie-recept na.

## v2.65.0

*English summary: English ingredients (many Mealie imports) can now be matched with the Dutch offers. Before comparing, each ingredient line is translated into Dutch from a built-in list of a few hundred common ingredients and phrases ("2 tbsp mayo" becomes "2 tbsp mayonaise", "chicken thighs" becomes "kippendijen", "kipdijfilet" and "kippendij"); nothing is sent to a translation service. A Dutch line is left as it is and the recipe card still shows the line as written in the recipe. A tablespoon of a dried spice ("2 tbsp paprika") is a seasoning and no longer looks for bell peppers.*

### Nieuw in 2.65.0

- **Engelse ingrediënten worden vertaald voor het vergelijken.** Een nieuwe module, `mealieshopper/offer_translate.py`, zet een Engelse regel om in het Nederlands, op woorden en uit een vaste lijst (een paar honderd gangbare ingrediënten en samenstellingen: groente en fruit, vlees en vis, zuivel, brood en pasta, sauzen, bakproducten, noten en specerijen). Aantallen, eenheden en wat tussen haakjes staat blijven staan; een meervoud (*tomatoes*, *cherries*) en hoofdletters maken niet uit; de langste frase wint (*cream cheese* wordt *roomkaas* en niet *room kaas*). Er gaat niets naar een vertaaldienst.
- **Meerdere Nederlandse namen worden allemaal geprobeerd.** De winkels noemen hetzelfde product niet gelijk: *chicken thighs* wordt *kippendijen*, *kipdijfilet* en *kippendij*, *ground beef* wordt *rundergehakt* en *gehakt*, *spring onion* wordt *bosui* en *lente-ui*. De aanbieding die het beste past wint.
- **De kaart toont de regel zoals hij in het recept staat** (Engels), met het Nederlandse product erachter, en *Niet in de aanbieding* noemt de oorspronkelijke regel.

### Aangepast in 2.65.0

- **Een eetlepel van een gedroogde specerij is een kruid.** *2 tbsp paprika*, *1 el kurkuma*, *1 tbsp cumin*, *2 el kerrie* en *1 tablespoon cinnamon* zoeken geen product meer bij de aanbiedingen. Voorheen werd *2 tbsp paprika* gekoppeld aan een zak paprika's, terwijl er paprikapoeder in een kruidenmix bedoeld werd. Een gewone *rode paprika* of *1 paprika* blijft een groente.

### Goed om te weten

- **De lijst is niet volledig.** Een Engels woord dat er niet in staat, blijft Engels en vindt dus meestal niets. Woorden die in beide talen gelijk zijn (*pasta*, *pesto*, *mozzarella*, *avocado*) hebben geen vertaling nodig. Ontbreekt een ingrediënt dat je vaak tegenkomt, geef het door; het is één regel in de lijst.
- Een Nederlandse regel verandert niet: de tests controleren dat voor een reeks gangbare Nederlandse regels, en dat opnieuw vertalen van een vertaalde regel niets verandert.
- Alleen de ingrediëntregels worden vertaald, niet de titels van recepten of de aanbiedingen (die zijn Nederlands).
- De API en de MCP-tools zijn niet veranderd.

## v2.64.0

*English summary: The products linked to a recipe's ingredients can be clicked. A dialog shows the product (picture, name, brand and size, the offer, the price with the old price, the conditions) and which ingredient from which recipe it is linked to, so you see at once whether the link is right. A button opens the store's own page in a separate window; Picnic has no web shop, so there the dialog says there is no product page. The dialog works with the keyboard (Enter opens, Esc closes), also closes with a click outside it, and fits a phone and the dark theme.*

### Nieuw in 2.64.0

- **Gekoppelde producten zijn aan te klikken.** Onder een receptvoorstel is `→ AH Zalmfilet 2-pack` nu een knop. Die opent een venster met het plaatje, de naam, het merk en de inhoud, de actie (*1+1 gratis*), de prijs met de oude prijs, de voorwaarden (*bij 2 stuks · € 2,50 per stuk · t/m 25 okt*) en de zin *Gekoppeld aan "100 g zalmfilet" uit Romige tagliatelle met zalm*. Zo zie je meteen of het product bij het ingrediënt past.
- **Bekijk bij de winkel.** Een knop *Bekijk bij Albert Heijn* of *Bekijk bij Jumbo* opent de pagina van het product in een los venster (hetzelfde als *Bekijk product* bij het mandje). Picnic heeft geen webwinkel; daar staat in het venster dat er geen productpagina is en ontbreekt de knop.
- Het venster sluit met *Sluiten*, met Esc of met een klik ernaast. Met het toetsenbord opent Enter op de productnaam het venster, en na het sluiten staat de focus weer op die naam. Het past op een telefoon en volgt het donkere thema.

### Goed om te weten

- Een productplaatje dat niet laadt wordt in het venster verborgen en niet uit de pagina gehaald (anders zou het venster daarna geen plaatjes meer kunnen tonen).
- Alleen bij de receptvoorstellen; de kaarten bij Aanbiedingen > Aanbiedingen openen nog steeds direct de winkelpagina in een nieuw tabblad.
- De API en de MCP-tools zijn niet veranderd.

## v2.63.1

*English summary: Fewer wrong links between recipe ingredients and offers. A kind of product ("curry sauce", "bread roll") only fits an offer that has the other words too, a raw product (bell pepper, chicken fillet) no longer fits a prepared one (crisps, focaccia, a wrap), shampoo, cat food and other non-food offers are left out, and Lay's/Bifi and similar snack brands are snacks instead of "vegetables". A line with a teaspoon or a pinch, a spice powder, English basics (salt, oil, flour ...) and a heading such as "Spice mixture (below)" no longer count as ingredients, and the same ingredient twice in a recipe counts once. In the two recipes that were reported, "5 of 20" became 1 of 8 (and is no longer suggested), and the shampoo is gone from the sushi bowl.*

### Opgelost in 2.63.1

- **Verkeerde koppelingen in de receptvoorstellen**, uit twee gemelde recepten:
  - *Bread roll* hoorde bij *Bifi Roll XXL*, *1 tbsp curry sauce* bij *Sweet chilli sauce*. Bij een soort (*saus*, *sauce*, *soep*, *kaas*, *brood*, *rol*, *worst*, *dressing*, *drank*, *vlees*, *vis*) moeten de andere woorden van het ingrediënt ook in de aanbieding staan.
  - *Paprika* hoorde bij *Lay's Paprika* en bij *AH Focaccia paprika olijf*. Een rauw product (groente en fruit, vlees, vis, zuivel en eieren, kaas en vleeswaren) past niet bij een bereid product (brood en bakkerij, maaltijden, snacks). Lay's, Doritos, Pringles, Cheetos, Bifi, Bugles en Tostitos zijn nu snacks; voorheen telde *Lay's Paprika* als groente. Dat verbetert ook de categorieën bij de aanbiedingen.
  - *Avocado's* hoorde bij *Avocado olie shampoo*. Aanbiedingen van drogisterie, huishouden, baby en huisdier doen niet meer mee aan de receptvoorstellen.
- **Wat geen ingrediënt is, telt niet meer mee** in *x van y* en in *Niet in de aanbieding*: een regel met een theelepel of snuf (*1 tsp salt*, *½ tsp cayenne*, *2 tl kurkuma*, *1 snuf nootmuskaat*), kruidenpoeders (*onion powder*, *knoflookpoeder*), de Engelse basis (*salt*, *pepper*, *oil*, *butter*, *flour*, *sugar*, *vinegar*, *stock*, *cornstarch*, *maizena*) en een kopje als *Spice mixture (below)* of *Kruidenmix (hieronder)*. Engelse eenheden en bijwoorden (*tbsp*, *tsp*, *cup*, *and*, *with*, *chopped*, ...) tellen niet als woorden van het ingrediënt, en een verbogen bijvoeglijk woord (*geraspte*, *gerookte*) telt net als *geraspt* niet mee.
- **Hetzelfde ingrediënt twee keer in een recept telt één keer** (*1 tsp paprika* en *2 tbsp paprika* waren twee ingrediënten met twee verschillende aanbiedingen).
- In de twee gemelde recepten werd *Grillworst* van "5 van 20" naar 1 van 8 (en wordt daarom niet meer voorgesteld, de enige koppeling is *mayo* → *Hellmann's*), en de *Warme sushibowl* verloor de shampoo.

### Goed om te weten

- **Engelse receptregels** (veel Mealie-imports) kunnen niet met Nederlandse aanbiedingen worden vergeleken: alleen woorden die in beide talen gelijk zijn (*paprika*, *mayo*, *avocado*) matchen. Zo'n recept toont nu minder valse koppelingen, maar blijft meestal onder de ondergrens.
- *Komkommer* hoort nog bij *AH Tomaat komkommer M* (een titel met een tweede groente); of dat klopt, is uit de titel alleen niet te zien. Gereedschap als *grillpan* telt nog als ingrediënt dat niet in de aanbieding is.
- De API en de MCP-tools zijn niet veranderd.

## v2.63.0

*English summary: Settings > Updates is now Settings > System. Under the update check there is a new Clean-up block with a factory reset. It warns about the consequences, asks for the word RESET and for the administrator to prove again who they are (password and, with two-step verification, the code; or a passkey), then deletes the whole database and the secret key, logs everybody out and restarts the app (under gunicorn with a restart policy), which starts at the onboarding again.*

### Nieuw in 2.63.0

- **Fabrieksinstellingen.** Onder *Instellingen > Systeem > Opschonen* zet de beheerder MealieShopper terug naar een nieuwe installatie. Dat verwijdert de database (gebruikers, wachtwoorden, passkeys, koppelingen, lijstjes, bestellingen, aanbiedingen, auditlog) en `secret.key`. De knop opent eerst een waarschuwing met de gevolgen en vraagt daarna om het woord `RESET`, om opnieuw te bewijzen wie je bent (wachtwoord, en bij tweestapsverificatie ook de code of een herstelcode; of een passkey) en om een laatste bevestiging. Foute pogingen tellen mee voor dezelfde begrenzing als inloggen.
- **Opnieuw starten.** Na het wissen stopt de app zichzelf (onder gunicorn, zoals in de container; de container start door het restart-beleid weer op) en laadt de pagina vanzelf opnieuw zodra de app terug is, bij de onboarding. Draait de app op een andere manier, dan meldt hij dat je hem zelf opnieuw moet starten.
- **Nieuwe routes** (alleen beheerder): `GET /api/system/factory-reset`, `POST /api/system/factory-reset/options` (de passkey-uitdaging) en `POST /api/system/factory-reset`.

### Aangepast in 2.63.0

- Het tabblad *Updates* in de Instellingen heet nu *Systeem* en bevat de updatecheck en het nieuwe blok *Opschonen*.
- De passkey-controle van het inloggen staat nu in `auth.check_assertion`, zodat het wissen dezelfde controle gebruikt.

## v2.62.0

*English summary: The recipe suggestions are about what you can cook with the offers of the chosen store. Above the recipes it now says so ("Recipes with as many ingredients as possible from the offers at Jumbo"), basics such as salt, oil, butter and flour no longer count towards "x of y ingredients on offer", the recipes with the largest share on offer come first, and each recipe lists what is not on offer. At the top there is a filter for the kind of dish (breakfast, lunch, starter, soup, main course, side dish, dessert, snack, other) with the number of recipes per kind. The kind comes from the recipe's source, so read the recipes once more after updating. A recipe card now says where the recipe comes from ("Recipe from Albert Heijn") instead of a bare store name that read like the store of the offers.*

### Nieuw in 2.62.0

- **Filter op soort gerecht.** Bovenaan Aanbiedingen > Recepten staat een rij keuzes: ontbijt, lunch, voorgerecht, soep, hoofdgerecht, bijgerecht, dessert, snack en overig, met het aantal recepten per soort. Alleen soorten die voorkomen staan erbij. De aantallen gelden voor alle voorstellen van de gekozen winkel, het filter beperkt de lijst. Een andere winkel zet het filter terug op alles. De soort komt uit de bron: de gang bij Jumbo, de categorieën en tags in Mealie en de categorie van het recept bij AH. Picnic geeft geen gang mee; die recepten staan onder overig.
- **Boven de recepten staat van welke winkel de aanbiedingen zijn** (*Recepten met zoveel mogelijk ingrediënten uit de aanbiedingen van Jumbo*), met de uitleg dat basisingrediënten niet meetellen en de recepten met de meeste ingrediënten in de aanbieding bovenaan staan.
- **Wat er niet in de aanbieding is.** Onder de gekoppelde ingrediënten staat *Niet in de aanbieding: …*, zodat je ziet wat je er nog bij moet halen.
- `GET /api/offers/recipes` krijgt `course` (een van de soorten, of `all`) en geeft per recept `course` en `missing`, en `courses` met de aantallen per soort.

### Aangepast in 2.62.0

- **Basisingrediënten tellen niet meer mee.** Zout, peper, water, ijs, olie, boter, bloem, suiker, azijn, bouillon en kruiden zijn niet langer een ingrediënt dat in de aanbieding hoort te zijn. Een regel telt als basis als het hoofdwoord er een is of erop eindigt (*milde olijfolie*, *roomboter*, *zeezout*, *balsamicoazijn*). Het aantal in *x van y ingrediënten in de aanbieding* wordt daardoor kleiner (*2 van 6* in plaats van *2 van 7*). Een smaakwoord aan het eind van een langere regel (*zuivelspread knoflook en kruiden*) telt nog mee als ingrediënt dat niet te koppelen is.
- **De receptkaart zegt waar het recept vandaan komt.** De winkelkeuze bij Aanbiedingen > Recepten bepaalt alleen met welke aanbiedingen de recepten worden vergeleken; de recepten komen uit al je bronnen. Achter het aantal ingrediënten stond de bron van het recept ("· Albert Heijn"), wat las alsof het AH-aanbiedingen waren, ook als je Jumbo had gekozen. Nu staat er *Recept uit Albert Heijn*.

### Goed om te weten

- **Lees je recepten één keer opnieuw in** (`Recepten inlezen`): de soort wordt daarbij bewaard. Tot dan staan alle recepten onder *overig*. AH-recepten die al in de index staan maar nog geen soort hebben, worden bij het inlezen opnieuw opgehaald, binnen de 60 nieuwe AH-recepten per keer.
- De volgorde is: het grootste deel van de ingrediënten in de aanbieding, dan het grootste aantal, dan de grootste besparing. Een recept moet minstens twee koppelingen hebben (één bij een recept met hooguit drie ingrediënten).
- De soorten komen uit vrije tekst van de bron en worden op woorden herkend (*Avondeten* en *Main course* zijn hoofdgerecht). Een categorie die je zelf in Mealie hebt bedacht en niet herkend wordt, valt onder overig.

## v2.61.0

*English summary: Offers now say what they involve and when they stop. Each offer shows from how many items it applies, what an item then costs and until when (AH gives an end date). The offer text is read for the kinds seen in the explorer reports, including Picnic's "2e = 50% korting" (the second item at 50% off, which was read as 50% off everything) and "nu €1,99". Offers whose end date has passed no longer show up in the list or the recipe suggestions between two syncs. The saving in recipe suggestions uses the price per item under the offer's condition instead of the difference between the old and the current price the store names, which can be wrong for "1+1 free" or "2nd half price". Ingredients are also linked to offers more strictly (a product that only contains the ingredient, such as mince with onion or tuna in olive oil, no longer counts). This brings the v2.53.0 work from `main` (the `deals` module, the `promo` fields and the offer part of the explorers) into the offers of `beta`.*

### Nieuw in 2.61.0

- **De voorwaarden bij een aanbieding.** Op de kaart staat naast het label (bijvoorbeeld *1+1 gratis*) nu *bij 2 stuks*, de prijs per stuk bij die voorwaarde en *t/m 25 okt* als de winkel een einddatum geeft. Dat is wat in het eerste rapport ontbrak: een aanbieding "2 voor 5" liet alleen de prijs zien.
- **Verlopen aanbiedingen verdwijnen.** Staat de einddatum in het verleden, dan laat de lijst de aanbieding niet meer zien en telt ze niet mee in de receptvoorstellen, ook al is er nog niet opnieuw gesynchroniseerd. `GET /api/offers` meldt hoeveel er zijn verborgen (`expired`). Bij AH komt de einddatum van de winkel zelf (`bonusEndDate`); Picnic en Jumbo geven geen datum, dus die blijven staan tot de volgende synchronisatie.
- **De module `mealieshopper/deals.py`** (uit v2.53.0 van `main`) leest de actietekst en rekent de prijs per stuk uit. Nieuw is *2e = 50% korting* (Picnic), dat als percentage over alles werd gelezen, en *nu €1,99*.

### Opgelost in 2.61.0

- **Verkeerd gekoppelde ingrediënten in de receptvoorstellen.** Een ingrediënt hoorde bij een aanbieding zodra zijn woord *ergens* in de titel stond. Bij *Romige tagliatelle met zalm* stond daardoor "7 van 7 ingrediënten in de aanbieding" met onder meer *AH Gehakt met ui* voor de ui, *Champignon saus romig en vol* voor de champignons, *Tonijnstukken in olijfolie* voor de olijfolie, *Spinazie met room* voor de verse spinazie en *Kruidenmix gehakt* voor de kruiden, en een besparing van € 11,41. Nu geldt:
  - de titel van de aanbieding wordt gesplitst bij *met*, *in*, *op*, *zonder*, *voor*, *en* en *&*; alleen het deel ervoor is het product. Wat erachter staat telt alleen als het ingrediënt het zelf noemt;
  - een bereide variant (*saus*, *soep*, *mix*, *maaltijd*, *salade*, *sap*, *pizza*, *spread* en een paar meer) telt alleen als het ingrediënt dat woord zelf bevat;
  - een smaakwoord (*kruiden*, *specerijen*) zoekt geen product;
  - past er meer dan één aanbieding, dan wint die met de minste woorden eromheen, niet de eerste in de lijst.
  Het voorbeeld houdt nu 2 van 7 over (tagliatelle en zalmfilet); de besparing telt alleen die twee.
- **Het hoofdwoord van een ingrediënt** stopt bij *met*, *in*, *op*, *zonder*, *voor* en *van*: bij *gehakt met ui* is dat gehakt (voorheen ui), bij *kipfilet in blokjes* kipfilet (voorheen blokjes) en bij *olie voor het bakken* olie.
- **Sinaasappel was ook sinaasappelsap.** De controle op afgeleide producten haalde de *s* van *sap* weg als voegklank. Daardoor telde *Sinaasappelsap* voor *sinaasappels*.

### Aangepast in 2.61.0

- **De besparing in de receptvoorstellen** rekent met de prijs per stuk onder de voorwaarde van de aanbieding. Voorheen was het de oude minus de huidige prijs die de winkel noemt; bij *1+1 gratis* op € 4,50 leidde dat tot € 1,50 in plaats van € 2,25. Zonder herkende aanbieding of zonder gewone prijs is het gedrag zoals eerder.
- De uitleg bij de verkenners van AH, Picnic en Jumbo noemt het onderdeel *aanbiedingen*.

### Goed om te weten

- Bij AH is niet bevestigd of `currentPrice` bij een aanbieding met voorwaarde de gewone prijs of de actieprijs is. De besparing gebruikt daarom de gewone prijs (`priceBeforeBonus`) en de actietekst, en blijft op nul als een van beide ontbreekt.
- De API en de MCP-tools zijn niet veranderd, op de extra velden `mechanic`, `minQuantity`, `unitPrice`, `validity` en `expired` bij `GET /api/offers` na.

## v2.60.2

*English summary: Picnic's offers are now read from where the explorer report showed them: the discount text next to products on the home page ("1+1 free", "20% off", "2 for €5", "now €1.99") and the action pages behind the cards under "Acties van de week" (`promo-group-deep-dive`), where every product is an offer. The label is the tile's text, or else the card's title. Picnic gives no old price.*

### Aangepast in 2.60.2

- **Picnic-aanbiedingen komen nu uit de startpagina.** Het rapport van de verkenner liet zien hoe ze erin staan: bij sommige producttegels staat een kortingtekst ernaast (*1+1 gratis*, *20% korting*, *2 voor €5*, *nu €1.99*, *2e = 50% korting*), en de kaarten onder *Acties van de week* openen een actiepagina (`promo-group-deep-dive`) met de producten van die actie. MealieShopper neemt de producten met een kortingtekst en leest die actiepagina's (met hun parameters, en de pagina's die ze zelf later laden), waar alle producten aanbiedingen zijn. Het label is de kortingtekst van de tegel, of anders de titel van de kaart (bijvoorbeeld *Appels en peren*). Recepten en andere pagina's worden niet gevolgd.
- **Rustig bij Picnic.** Tussen de pagina's zit een korte pauze en er worden hooguit veertig pagina's per synchronisatie gelezen, zodat Picnic niet tijdelijk blokkeert.
- De verwijzingen in de app komen met `nl.picnic-supermarkt://` en een parameter erachter; die worden nu herkend en doorgegeven.
- De Picnic-verkenner leest nu ook de eerste drie actiepagina's, voor als hun opbouw anders blijkt dan verwacht.

### Goed om te weten

- Picnic geeft geen oude prijs bij een aanbieding, dus *oude prijs* ontbreekt; de prijs is die van de tegel.
- Dit is gebouwd op het verkenner-rapport, zonder de actiepagina's zelf te hebben gezien. Geeft een synchronisatie weinig of niets, deel dan het deel van het rapport met *aanbiedingen: GET pages/promo-group-deep-dive...*.

## v2.60.1

*English summary: The Picnic explorer is lighter (no more than a few dozen requests instead of more than a hundred, so Picnic no longer blocks the network halfway through; the old favorites exploration needs `?extended=1`) and reports more about the offers on the home page: the texts next to products and where the "Acties" cards lead.*

### Aangepast in 2.60.1

- **De Picnic-verkenner stuurt veel minder verzoeken.** Het vorige rapport liep halverwege vast op een 403: de verkenning van de favorieten-pagina's stuurde ruim honderd verzoeken achter elkaar en Picnic blokkeert dan tijdelijk. Die verkenning (de favorieten zijn klaar) draait nu alleen met `POST /api/picnic/explore?extended=1`; het gewone rapport blijft ruim onder de veertig verzoeken.
- **Meer over de Picnic-aanbiedingen in het rapport.** Het rapport van de startpagina liet zien dat de aanbiedingen onder *Acties van de week* kaarten per merk of productgroep zijn (geen producttegels) en dat de producttegels geen korting-teken hebben. Het rapport meldt nu ook welke kortingachtige teksten bij de tegels staan (zoals een percentage of een actieprijs, met cijfers als #, zonder productnamen) en waar de kaarten onder *Acties* naartoe leiden (vorm van hun actie en verwijzing, zonder inhoud).

### Goed om te weten

- Picnic-aanbiedingen worden nog niet gevonden: de herkenning wacht op dit rapport.

## v2.60.0

*English summary: The Offers tab shows categories with a count ("5x Vegetables & Fruit", "10x Meat") for every store; clicking one shows its offers. Three rows show first and a "More" button adds three rows at a time. The "Fetch offers" button is gone: the offers come from the local copy, refreshed daily or via Settings > Link. Every sync now also writes the offers to a permanent history in the database (when, price, old price, label, validity), available through `GET /api/offers/history`.*

### Nieuw in 2.60.0

- **Categorieën bij de aanbiedingen, voor alle winkels.** Boven de aanbiedingen staat per categorie een knop met het aantal ("5x Groente & Fruit", "10x Vlees", "9x Frisdrank & Sap"), plus **Alle**. Een klik toont de aanbiedingen van die categorie. De indeling (negentien categoriën, van Groente & Fruit tot Huisdier en Overig) maakt MealieShopper zelf uit de productnaam, omdat AH, Picnic en Jumbo geen gelijke categorieën geven; een categorie-hint van de winkel telt alleen mee als de naam niets zegt. De categorie wordt bij het synchroniseren bewaard.
- **Drie rijen, dan Meer.** Er staan eerst drie rijen aanbiedingen (het aantal per rij hangt van de breedte af); de knop **Meer** toont er steeds drie extra.
- **Geschiedenis van de aanbiedingen.** Elke synchronisatie schrijft de aanbiedingen ook in een geschiedenis in de database, voor altijd bewaard: per product wanneer het in de aanbieding was, met prijs, oude prijs, label, geldigheid, categorie en het aantal synchronisaties dat het erbij stond. Een aaneengesloten periode telt als een keer; een prijswijziging tijdens dezelfde aanbieding niet; komt een product na een onderbreking terug, dan is dat een nieuwe keer. `GET /api/offers/history` (met `store`, `q` en `limit`) toont per product hoe vaak het in de aanbieding was en de details. Het ophalen blijft de lokale kopie vervangen, en mislukt het ophalen dan blijven de kopie en de geschiedenis ongewijzigd.

### Verwijderd in 2.60.0

- **De knop *Aanbiedingen ophalen* op het tabblad Aanbiedingen.** De aanbiedingen komen uit de lokale kopie, die elke dag wordt ververst; de beheerder synchroniseert ze meteen onder **Instellingen > Koppelen**.

### Goed om te weten

- Een database van een eerdere versie wordt vanzelf bijgewerkt (de kolom *categorie* komt erbij); bestaande aanbiedingen krijgen hun categorie uit de naam. De geschiedenis begint bij de eerstvolgende synchronisatie.
- Jumbo-aanbiedingen worden via zoekopdrachten gevonden; een product dat bij een synchronisatie net niet in die zoekresultaten staat telt als weg en als een nieuwe keer als het terugkomt.
- De MCP-tools zijn niet veranderd.

## v2.59.1

*English summary: When Picnic answers the login with a bare "403 Forbidden" page (the gateway in front of the API refusing this network, not a wrong password), MealieShopper now says so and what to try, instead of the generic "Picnic error". Picnic's offers are now looked for on the home page (Discover), because the app has no offers tab; the Picnic explorer reports on those pages first.*

### Aangepast in 2.59.1

- **Duidelijke melding als Picnic het netwerk weert.** Antwoordt Picnic bij het inloggen met een kale *403 Forbidden*-pagina (geen JSON), dan komt dat van de poort vóór de API en niet van de login zelf. MealieShopper meldt nu dat Picnic verbindingen vanaf dit netwerk weigert, dat het niet aan je e-mailadres of wachtwoord ligt, en dat je het later of vanaf een thuisnetwerk kunt proberen (Picnic weert soms server- en datacenter-adressen en blokkeert tijdelijk na veel pogingen). Voorheen stond er alleen *Picnic fout*. Een 403 die de API zelf als JSON geeft blijft een gewone fout.

- **Picnic-aanbiedingen zoeken op de startpagina.** De verkenner liet zien dat de tabbalk van de Picnic-app geen aanbiedingen-tab heeft (alleen Ontdek, Favorieten, Koken, Zoeken en Mandje), dus de eerdere zoektocht naar zo'n tab vond niets. MealieShopper leest nu de startpagina (*Ontdek*) en de pagina's waar die over aanbiedingen naar verwijst, ook de pagina's die ze zelf later laadt, en houdt alleen producten over met een korting-teken (een oude prijs, prijsklassen of een actielabel). Staan er geen aanbiedingen, dan meldt MealieShopper dat en vraagt het rapport van de verkenner.
- **De Picnic-verkenner begint met de aanbiedingen.** Bovenaan het rapport staat per pagina de opbouw, hoeveel producttegels een korting-teken hebben, welke velden en decorators ze hebben en welke pagina's er later laden. Zonder namen, prijzen of id's.

### Goed om te weten

- Het inloggen zelf is niet veranderd, alleen wat je te zien krijgt als Picnic het netwerk weigert. Dat het inloggen daarna weer lukte past bij een tijdelijke blokkade.
- Welk veld Picnic bij een productkorting precies gebruikt is nog niet bekend: de verkenner heeft er een tegel zonder korting van laten zien. Draai hem opnieuw als de lijst leeg blijft.

## v2.59.0

*English summary: The offers of Picnic and Jumbo are now fetched and shown on the Offers tab, next to Albert Heijn. A background task fetches the offers of all stores once a day (default around 5 am; `MEALIESHOPPER_OFFERS_SYNC_HOUR`, switch off with `MEALIESHOPPER_OFFERS_SCHEDULE=false`), and under Settings > Link a new "Sync offers" button fetches them right away. Picnic's offers come from the app's offers tab; Jumbo has no route for all offers, so they are found by searching common terms and keeping products with an action.*

### Nieuw in 2.59.0

- **Aanbiedingen van Picnic en Jumbo.** Het tabblad **Aanbiedingen** toont nu ook de aanbiedingen van Picnic en Jumbo, zodra die gekoppeld zijn, met de oude en de nieuwe prijs en het label. Picnic wordt gelezen van de aanbiedingen-tab van de app (via `/bootstrap`, met een paar bekende paginanamen als terugval). Jumbo heeft geen route die alles in een keer geeft: MealieShopper zoekt op ruim vijftig gangbare termen en houdt de producten met een actieprijs of actielabel over. Dat zijn dus de aanbiedingen bij die termen, niet per se alle.
- **Elke dag vanzelf.** Een achtergrondtaak haalt een keer per dag de aanbiedingen van alle winkels op, standaard rond 5 uur (tijd van de container). Is de app dan uit, dan volgt het bij de volgende start; meerdere workers voeren de dagelijkse run maar een keer uit. Het uur stel je in met `MEALIESHOPPER_OFFERS_SYNC_HOUR`, uitzetten kan met `MEALIESHOPPER_OFFERS_SCHEDULE=false`.
- **Synchroniseren onder Instellingen > Koppelen.** Een nieuw blok **Aanbiedingen** met de knop **Aanbiedingen synchroniseren** (alleen de beheerder) haalt de aanbiedingen van alle winkels meteen op. Eronder staat per winkel hoeveel aanbiedingen er zijn en wanneer ze zijn opgehaald, of dat de winkel niet gekoppeld is of het ophalen mislukte, plus wanneer de dagelijkse taak voor het laatst liep en weer loopt.
- Nieuwe routes: `GET /api/offers/status` en `POST /api/offers/sync-all` (alleen de beheerder).

### Aangepast in 2.59.0

- Een winkel die niet gekoppeld is of waar het ophalen mislukt blokkeert de andere winkels niet; de vorige kopie van die winkel blijft staan.

### Goed om te weten

- De routes van Picnic en Jumbo zijn onofficieel en kunnen veranderen. Geeft een winkel niets terug, dan staat dat als melding bij Koppelen en bij Aanbiedingen.
- De MCP-tools zijn niet veranderd.

## v2.58.0

*English summary: On a large screen (1024 px and wider) the main navigation now sits in a vertical menu on the left instead of above the page. It can be collapsed to icons only (with the name as a tooltip) with the button at the bottom of the menu; the choice is remembered. On smaller screens nothing changes.*

### Nieuw in 2.58.0

- **Navigatiemenu links op een groot scherm.** Vanaf 1024 px breed staan Recepten, Aanbiedingen, URL importeren, Winkels en Mandje onder elkaar in een menu links van de pagina, dat meescrolt. Op kleinere schermen staat het menu zoals altijd boven de pagina, en in de app op een telefoon als menubalk onderaan.
- **Inklapbaar.** Onderaan het menu klapt **Menu inklappen** het in tot alleen de pictogrammen (de naam staat dan als tooltip); met dezelfde knop klap je het weer uit. MealieShopper onthoudt je keuze.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd.

## v2.57.0

*English summary: The week menu loads by itself when you open it, and the two following weeks are fetched lazily in the background; every week is cached for 30 minutes, so paging through weeks is instant. "Load week menu" is now a sync icon next to the week. The store picker and "Add to basket" moved below the shopping list, and the picker uses the same drop-down as the store picker on Stores. The Basket tab moved to the right of Stores.*

### Nieuw in 2.57.0

- **Het weekmenu laadt vanzelf.** Zodra Mealie gekoppeld is en je het weekmenu opent, staat de week er al; je hoeft niet meer op een knop te drukken. De twee weken erna worden rustig op de achtergrond opgehaald.
- **Weken worden onthouden.** Een opgehaalde week blijft 30 minuten bewaard, dus vorige en volgende week openen direct. Bladeren met de pijltjes laadt de week zelf.

### Aangepast in 2.57.0

- **Synchroniseren in plaats van "Weekmenu laden".** Naast de week staat nu een synchroniseericoon (hetzelfde als bij het Mandje) dat de week opnieuw uit Mealie haalt; de tekst is weg.
- **Winkelkeuze en "Voeg toe aan mandje" onder het boodschappenlijstje.** Ze stonden erboven; nu staan ze eronder. De winkelkeuze is een uitklapmenu in dezelfde stijl als de winkelkeuze bij **Winkels**.
- **Het tabblad Mandje staat rechts van Winkels** (voorheen tweede, na Recepten), ook in de onderste menubalk van de app.

### Goed om te weten

- De API en de MCP-tools zijn niet veranderd.
- Pull requests: #96.

## v2.56.1

*English summary: On Settings > Link the "linked" banners are now as wide as the collapsible blocks below them. On small screens the banners show an icon and the name instead of the text, coloured by status (link icon on green = linked, check mark on dark green = linked and working, crossed-out link on red = not linked), two next to each other. The AH banner now says "Albert Heijn linked" and the Mealie banner just "Mealie linked" (no address). "About" moved from the tab bar into the avatar menu, between Settings and Log out. The blocks are titled just Mealie, Albert Heijn, Jumbo and Picnic (instead of "Link Mealie" and "Log in to ..."), and Jumbo now comes before Picnic, also in the banners.*

### Aangepast in 2.56.1

- **De "gekoppeld"-meldingen zijn even breed als de blokken eronder** (ze liepen over de volle breedte).
- **Kortere titels op de blokken:** *Mealie*, *Albert Heijn*, *Jumbo* en *Picnic* in plaats van *Mealie koppelen* en *Inloggen bij ...*.
- **Nieuwe volgorde:** Albert Heijn, Jumbo, Picnic, zowel bij de meldingen als bij de blokken.
- **Over zit nu in het avatar-menu**, tussen *Instellingen* en *Uitloggen*; de tab Over is uit de tabbalk (die heeft nu zes plekken). Op een klein scherm lichtt de avatar op als Over open is.
- **De Mealie-melding zegt nu *Mealie gekoppeld*** (zonder het adres).
- **Op een klein scherm staan er pictogrammen in de meldingen** in plaats van *gekoppeld*, *nog niet gekoppeld* en *gekoppeld en werkt*: een schakel op groen (gekoppeld), een vinkje op donkergroen (gekoppeld en werkt) en een doorgestreepte schakel op rood (niet gekoppeld), met de naam van de dienst. Ook op een smal scherm staan twee meldingen naast elkaar. De tekst blijft voor schermlezers staan.
- **De AH-melding zegt nu *Albert Heijn gekoppeld*** (was *AH account gekoppeld*), net als *Albert Heijn nog niet gekoppeld* en *Albert Heijn gekoppeld en werkt*.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-31).

### Goed om te weten

- Verder verandert er niets.

## v2.56.0

*English summary: The "Logged in as ..." chip is replaced by an avatar with a drop-down menu (Profile, Settings for the administrator, Log out). On small screens the avatar sits in the tab bar in place of Settings. Profile has General (display name and avatar upload), Security (one Password block with Two-step verification inside it, plus Passkeys) and AI apps (for everyone). Settings, administrator only, has Link, Users, Audit log and Updates. The main tab Settings is gone.*

### Nieuw in 2.56.0

- **Avatar met uitklapmenu in plaats van "Ingelogd als ...".** Menu: **Profiel**, **Instellingen** (alleen de beheerder) en **Uitloggen**. Het werkt ook zonder inloggen (dan zonder Uitloggen), zodat je het wachtwoord en de koppelingen kunt instellen. Op een klein scherm staat de avatar in de tabbalk op de plek van het Beheer-icoon.
- **Profiel > Algemeen:** je displaynaam en een avatar uploaden (PNG, JPEG of WebP; de browser verkleint hem tot 256 x 256, hoogstens 256 kB, geen SVG). Zonder avatar zie je je initialen. De displaynaam staat ook in Instellingen > Gebruikers, met de avatar.
- **Profiel > Beveiliging:** het blok *Wachtwoord* bevat nu ook *Tweestapsverificatie* (geen eigen blok meer), met daaronder *Passkeys*.
- **Profiel > AI-apps** is voor iedereen.
- **Instellingen** (alleen de beheerder): *Koppelen*, *Gebruikers*, *Auditlog* en *Updates*. Het bolletje bij een nieuwe versie staat nu op de avatar en bij Instellingen.
- **Nieuwe routes:** `GET/PUT /api/profile`, `POST/DELETE /api/profile/avatar` en `GET /api/profile/avatar/<gebruiker>`. De avatar staat in de database; het type wordt aan de inhoud gecontroleerd.

### Aangepast in 2.56.0

- **De hoofdtab Beheer is vervallen.** Teksten die ernaar wezen zeggen nu *Instellingen > Koppelen*, *Profiel > Beveiliging* en *Profiel > AI-apps*. De onderste menubalk heeft nog zeven plekken: de avatar staat waar Beheer stond.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-29).

### Goed om te weten

- Verder verandert er niets aan het koppelen, de MCP-tools of de rechten. De screenshots in `docs/screenshots/` laten nog de oude indeling zien.

## v2.55.0

*English summary: Settings > Link is now a plain sub-tab with one page instead of a drop-down menu. The page shows the four "linked" banners (Mealie, Albert Heijn, Picnic, Jumbo) on top, two next to each other, followed by one collapsible block per service: Mealie first, the stores below it. The Unlink button now sits next to the Link button inside the block of the store (together with Check link). The Mealie block is collapsible too and folds up once Mealie is linked.*

### Aangepast in 2.55.0

- **Koppelen is een gewoon subtab** (geen uitklapmenu meer) en toont direct de koppelpagina. De aparte pagina's per winkel en de pagina Mealie zijn samengevoegd: bovenaan de statussen, daaronder de blokken **Mealie koppelen**, **Inloggen bij Albert Heijn**, **Inloggen bij Picnic** en **Inloggen bij Jumbo**.
- **De vier "gekoppeld"-meldingen staan bovenaan, twee naast elkaar** (op een smal scherm onder elkaar).
- **Het blok Mealie is ook inklapbaar** en klapt in zodra Mealie gekoppeld is, net als de blokken van de winkels. De knop *Mealie koppelen* in de melding bovenaan Weekmenu opent het blok altijd.
- **Ontkoppelen staat naast Koppelen.** In het blok van elke winkel staan *Koppelen* (bij Picnic *Inloggen bij Picnic*), *Ontkoppelen* (Picnic, Jumbo en Mealie) en *Koppeling controleren* naast elkaar, zichtbaar zodra de winkel gekoppeld is.
- Eerder in deze ronde (niet apart uitgebracht): menu Koppelen met de winkels, uniforme titels *Inloggen bij ...*, inklapbare blokken en de melding bovenaan.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-28).

### Goed om te weten

- Verder verandert er niets aan het koppelen, de verkenners, de MCP-tools of de API. De screenshots in `docs/screenshots/` laten nog de oude indeling zien.

## v2.54.0

*English summary: Settings > Stores is now Settings > **Link**: one drop-down menu with Albert Heijn, Picnic, Jumbo and Mealie (Mealie no longer has its own tab). The three store pages have the same layout: the "account linked" banner (with Check link, and Unlink for Picnic and Jumbo) on top, and one collapsible block "Log in to ..." that also holds "Finish linking" and the route explorer. The block folds up once the store is linked. The titles are now all "Log in to ...", where Albert Heijn still said "Log in with". Texts that point to Settings > Stores now say Settings > Link.*

### Aangepast in 2.54.0

- **Nieuw submenu Koppelen onder Beheer.** Het uitklapmenu met de winkels is verhuisd naar **Koppelen** en Mealie staat er nu ook in (het tabje Mealie is vervallen). Het menu bevat Albert Heijn, Picnic, Jumbo en Mealie en is alleen voor de beheerder.
- **Alle titels heten nu "Inloggen bij ...".** Bij Albert Heijn stond nog *Inloggen via Albert Heijn*.
- **Inklapbaar blok per winkel.** *Inloggen bij ...*, *Koppeling afronden* en *Routes verkennen* staan nu in een blok dat je in- en uitklapt. Het blok klapt vanzelf in zodra de winkel gekoppeld is en staat open zolang dat nog niet zo is (ook als Picnic om een bevestigingscode vraagt). Een keuze die je zelf maakt blijft staan tot je de pagina herlaadt.
- **De melding "account gekoppeld" staat bovenaan**, boven het blok, met de knoppen *Koppeling controleren* (en *Ontkoppelen* bij Picnic en Jumbo) eronder. Ze blijven zichtbaar als het blok is ingeklapt. De bevestigingscode van Picnic staat nu in het inklapbare blok.
- **Teksten die naar de plek wijzen** (meldingen van de app, de installatiehulp en de README's) zeggen nu *Beheer > Koppelen > ...* in plaats van *Beheer > Winkels > ...*.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-27), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- Verder verandert er niets aan het koppelen, de verkenners, de MCP-tools of de API. De screenshots in `docs/screenshots/` laten nog de oude indeling zien en volgen bij een volgende ronde.

## v2.53.1

*English summary: The Offers tab now follows the layout of the Stores tab: a store drop-down menu (shown once a second store is linked) followed by two sub-tabs, **Offers** and **Recipes**, instead of a store select box and two blocks below each other. Nothing changes in what the offers and the suggestions do.*

### Aangepast in 2.53.1

- **Aanbiedingen heeft dezelfde opzet als Winkels.** De winkelkeuze is nu het eerste uitklapmenu in de subtabbalk (het verschijnt zodra naast Albert Heijn ook Picnic of Jumbo gekoppeld is, en toont de gekozen winkel), gevolgd door de subtabs **Aanbiedingen** (de producten) en **Recepten** (de receptvoorstellen en `Recepten inlezen`). De keuzelijst boven de blokken is vervallen; de gekozen winkel wordt nog steeds onthouden.
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-26), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- Verder verandert er niets aan de aanbiedingen, de receptvoorstellen, de koppelingen of de API.

## v2.53.0 (beta, tabblad Aanbiedingen)

*English summary: New main tab **Offers**: shows the offers of a store (for now Albert Heijn Bonus, no AH link needed) and suggests recipes with ingredients on offer, from your Mealie recipes, Jumbo favourites, Picnic recipes and saved AH recipes. Matching runs locally, without an AI service. Picnic and Jumbo offers follow once their route is known: the explorers help find it. The main menu now has seven tabs, which also fits the installed app's bottom bar again.*

### Nieuw in 2.53.0

- **Tab Aanbiedingen.** Toont de aanbiedingen van een winkel met oude en nieuwe prijs en het label, bewaard in een lokale kopie met het tijdstip van ophalen. Nu voor Albert Heijn (Bonus, werkt zonder AH-koppeling); `Aanbiedingen ophalen` vernieuwt de kopie en bij een fout blijft de vorige staan.
- **Receptvoorstellen.** `Recepten inlezen` leest de ingrediënten van je Mealie-recepten, Jumbo-favorieten, Picnic-recepten en bewaarde AH-recepten in. Daarna toont het tabblad de recepten waarvan ingrediënten in de aanbieding zijn, gerangschikt op het aandeel in de aanbieding en de geschatte besparing. Het vergelijken gebeurt lokaal (zonder meervoud, eenheden en basisingrediënten als zout en water); er gaat niets naar een AI-dienst.
- **Nieuwe routes:** `GET /api/offers`, `POST /api/offers/sync`, `GET /api/offers/recipes` en `POST /api/offers/recipes/sync`.
- **AH-verkenner** (`POST /api/ah/explore`) probeert nu ook de bonuspagina en meldt de vorm van het antwoord en het aantal gevonden producten.

### Aangepast in 2.53.0

- **Zeven tabs in het hoofdmenu.** De onderste menubalk van de geïnstalleerde app heeft weer evenveel kolommen als tabs (die stond sinds 2.52.0 op zeven bij zes tabs).
- `MEALIESHOPER_ASSET_VERSION` is opgehoogd (20261023-25), zodat browsers de nieuwe bestanden laden.

### Goed om te weten

- De bonusroute van AH is onofficieel en nog niet getest tegen de echte dienst. Antwoordt de bonuspagina niet, dan zoekt MealieShopper producten met de bonusvlag; draai bij twijfel de AH-verkenner.
- Aanbiedingen van Picnic en Jumbo zijn er nog niet: voor beide moet eerst de juiste pagina of query worden gevonden (draai hun verkenner op je installatie).

## v2.53.0

*English summary: First step towards reliable offers for recipes, without AI. The stores' offers are no longer reduced to a yes/no flag: the offer text (such as "2nd half price" or "2 for 5"), the regular price and, where the store gives it, the period are kept, and a new module turns them into one shape with the kind of offer, the minimum quantity, the price per item and whether the offer is still valid. The check list of the week menu shows the offer and until when it applies, and says so when it has expired. The explorers of AH, Picnic and Jumbo now also report what an offer looks like (fields, offer texts and dates; no prices or products) so the store fields can be confirmed with real answers. Offers are not yet matched to recipes; that comes after the explorer reports.*

### Nieuw in 2.53.0

- **Aanbiedingen als eigen onderdeel.** De nieuwe module `mealieshopper/deals.py` leest de actietekst van de winkels ("2e halve prijs", "1+1 gratis", "2 voor 5,00", "25% korting", "€ 1,50 korting") en rekent uit wat één stuk kost als je de voorwaarde haalt. Wat niet met zekerheid te lezen is blijft "onbekend", zodat er geen verkeerde prijs verschijnt. Ook de looptijd wordt gecontroleerd: een aanbieding is geldig, verlopen, nog niet begonnen of zonder datums.
- **De actie, de gewone prijs en de looptijd blijven bewaard.** Bij AH, Jumbo en Picnic bevatten producten met een aanbieding nu een extra veld `promo`. Voorheen bleef alleen over of er een aanbieding was; Jumbo gooide de actietekst weg en AH de gewone prijs.
- **De verkenners laten aanbiedingen zien.** Bij **AH-routes**, **Picnic-routes** en **Jumbo-routes verkennen** staat nu een onderdeel "aanbiedingen" met de velden rond een aanbieding en de actieteksten en datums die de winkel gebruikt. Er staan geen prijzen of producten in, dus het rapport blijft veilig te delen. Bij Jumbo zoekt de verkenner zelf uit welke extra promotievelden bestaan.

### Aangepast in 2.53.0

- **De controlelijst van het weekmenu toont de aanbieding.** Bij een product met een aanbieding staat nu de actie erbij en tot wanneer die geldt ("2e halve prijs t/m 25 okt"). Is de aanbieding verlopen, dan staat er "aanbieding verlopen".

### Goed om te weten

- Of AH, Jumbo en Picnic de actietekst en looptijd echt meegeven en onder welke veldnamen, is nog niet met echte antwoorden bevestigd. Ontbreekt iets, dan blijft het leeg en toont de lijst alleen "Bonus". Draai de verkenners en deel het rapport om dit vast te stellen.
- Er worden nog geen aanbiedingen aan recepten gekoppeld; dat volgt na de verkennerrapporten.
- De API en de MCP-tools zijn niet veranderd, op het extra veld `deal` bij de producten van de controlelijst na.

## v2.52.0

*English summary: "Week menu to shop" and "Find recipes" are merged into one main tab, **Recipes**, with two subtabs: **Week menu** (renamed from "Week menu to shop") and **Find recipes**, both with an icon. The main menu goes from seven to six tabs.*

### Aangepast in 2.52.0

- **Eén tab Recepten met subtabs.** *Weekmenu naar winkel* en *Recepten zoeken* staan niet meer los in het hoofdmenu, maar als subtabs onder de nieuwe tab **Recepten**. Het hoofdmenu heeft daardoor zes tabs in plaats van zeven. Recepten opent op het weekmenu; het logo brengt je daar nog steeds naartoe.
- **Weekmenu naar winkel heet nu Weekmenu**, en beide subtabs hebben een Material Design-icoon (een kalender bij Weekmenu, een vergrootglas bij Recepten zoeken). De teksten zijn in het Nederlands en het Engels bijgewerkt en de README's noemen de nieuwe indeling.
- **De versie van de statische bestanden is opgehoogd** (`MEALIESHOPPER_ASSET_VERSION`, 20261023-23), zodat browsers en de geïnstalleerde app de nieuwe vertalingen en opmaak laden. Zonder dat toonde de tab de sleutel `nav.recipes` en waren de subtab-iconen te groot. De iconen hebben nu ook een vaste grootte.

### Goed om te weten

- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.

## v2.51.2

*English summary: The planner no longer offers "Jumbo (shopping list)": groceries for Jumbo always go in the Jumbo basket. A remembered choice of the removed option falls back to Albert Heijn. The Jumbo shopping list itself is still shown under Stores > Lists. The API route that wrote to that list is unchanged but no longer used by the app.*

### Verwijderd in 2.51.2

- **De keuze "Jumbo (boodschappenlijstje)" in de planner.** Bij het weekmenu kies je nog tussen Albert Heijn, Picnic en Jumbo; bij Jumbo gaan de ingrediënten altijd in het Jumbo-mandje, niet meer op het boodschappenlijstje. Had je die keuze onthouden, dan staat de planner voortaan op Albert Heijn. Het boodschappenlijstje van Jumbo blijft te zien onder **Winkels > Lijstjes**.
- De bijbehorende teksten zijn weggehaald (de knop, de melding na het toevoegen en de melding dat iets al op het lijstje stond) en de uitleg bij het koppelen van Jumbo noemt het lijstje niet meer als plek voor je boodschappen.

## v2.51.1

*English summary: The "Stores" menu under Settings no longer carries a fixed "Stores" title: its button shows the chosen store (Albert Heijn, Picnic or Jumbo), just like the store picker on the Stores page. The static-file version is raised so browsers load the new script.*

### Aangepast in 2.51.1

- **Beheer > Winkels zonder titel.** De knop van het winkelmenu onder Beheer toont niet meer de vaste titel *Winkels*, maar de gekozen winkel (*Albert Heijn*, *Picnic* of *Jumbo*), zodat de balk er hetzelfde uitziet als bij de winkelkeuze op de pagina **Winkels**. Voor schermlezers heet de knop nog steeds *Winkels*.

### Goed om te weten

- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: bouwt voort op #80 (winkels onder één menu bij Beheer) en #83 (winkelkeuze op Winkels).

## v2.51.0

*English summary: The store picker on the Stores page is now the first drop-down menu in the sub-tab bar, styled like the "Stores" menu under Settings, instead of a separate select box above it. The button shows the chosen store; with only one linked store it stays hidden, as before. The static-file version is raised again so browsers load the new scripts and styles.*

### Nieuw in 2.51.0

- **Winkelkeuze in het menu van Winkels.** De winkelkeuze bovenaan de pagina **Winkels** (Albert Heijn, Jumbo, Picnic) is geen losse keuzelijst meer, maar het eerste uitklapmenu in de subtabbalk, in dezelfde stijl als **Beheer > Winkels**. De knop toont de gekozen winkel; met één gekoppelde winkel blijft hij verborgen, zoals voorheen.

### Goed om te weten

- Het wisselen van winkel werkt zoals voorheen: Picnic begint bij de recepten, Albert Heijn en Jumbo bij de lijstjes.
- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: bouwt voort op #80 (winkels onder één menu bij Beheer) en #82 (versie 2.50.0).

## v2.50.0

*English summary: Version number set to 2.50.0. The static-file version (cache buster) is raised, so browsers and the installed app load the current scripts and translations. Without it, the new "Stores" menu under Settings (added in 2.12.0) could show its raw text key "settings.tab.stores" instead of "Stores"/"Winkels" until the cache was cleared. Nothing else changes.*

### Nieuw in 2.50.0

- **Versienummer 2.50.0.** De versie van MealieShopper is op verzoek van de beheerder op 2.50.0 gezet.
- **Winkelmenu toont weer "Winkels".** Na 2.12.0 kon de knop **Winkels** onder Beheer de sleutel `settings.tab.stores` tonen in plaats van de tekst, omdat een browser of de geïnstalleerde app de oude vertaalbestanden uit zijn cache bleef gebruiken. De versie van de statische bestanden (`MEALIESHOPPER_ASSET_VERSION`) is opgehoogd, zodat de nieuwe bestanden worden geladen.

### Goed om te weten

- Ziet een browser de sleutel toch nog, vernieuw de pagina dan een keer geforceerd (of sluit en open de app opnieuw).
- Verder verandert er niets aan wat de koppelingen, de MCP-tools of de API doen.
- Pull requests: de wijziging die hierop voortbouwt is #80 (winkels onder één menu bij Beheer, 2.12.0).

## v2.13.0

*English summary: The setup guide on the first start now also links Jumbo and Picnic. It has six steps instead of four: Security, Mealie, Albert Heijn, Jumbo, Picnic, Done. The Jumbo step works like Albert Heijn (log in on jumbo.com, paste the line from the browser console); the Picnic step takes your email address and password and, when Picnic asks for it, the code you get by text message or email, all in the same step. Both steps can be skipped, and the final overview lists them with a button to go back. An existing installation with login switched off that only has Jumbo or Picnic linked no longer gets the setup guide either.*

### Nieuw in 2.13.0

- **Jumbo en Picnic in de installatiehulp.** De installatiehulp op de eerste start heeft nu zes stappen: *Beveiliging*, *Mealie*, *Albert Heijn*, *Jumbo*, *Picnic* en *Klaar*. Je hoeft niet meer naar Beheer om je supermarkten te koppelen.
- **Jumbo koppelen.** Werkt net als Albert Heijn: de uitleg voor de inlog via de browserconsole, de knop **Inloggen bij Jumbo** en het plakveld staan in de stap zelf. Dit kan alleen op een computer; gebruik je Jumbo niet of zit je op een telefoon, dan sla je de stap over.
- **Picnic koppelen.** Vul je e-mailadres en wachtwoord in. Vraagt Picnic om een code, dan verschijnt in dezelfde stap een veld voor de code en wordt hij meteen per sms verstuurd; met de knoppen eronder vraag je hem opnieuw aan, of per e-mail. Is de login halverwege afgebroken (bijvoorbeeld door te verversen), dan staat het codeveld er na het openen van de stap weer. Je wachtwoord wordt niet bewaard.
- **Overzicht.** De laatste stap toont ook Jumbo en Picnic, met een knop om alsnog te koppelen als er een ontbreekt. Na afloop is de winkelkeuze in de planner meteen bijgewerkt.
- **Welkomsttekst en README.** De welkomsttekst en de waarschuwing bij *Zonder beveiliging verder* noemen nu alle supermarkten, en de sectie *Eerste start* van de README beschrijft de zes stappen.

### Goed om te weten

- Beide stappen zijn optioneel en gebruiken dezelfde koppelingen als **Beheer > Winkels**; wat je in de installatiehulp koppelt, kun je daar later gewoon wijzigen of ontkoppelen.
- Een bestaande installatie met inloggen uit die al aan Jumbo of Picnic gekoppeld is (en dus niet aan Mealie of Albert Heijn), krijgt de installatiehulp niet meer te zien.

## v2.12.0

*English summary: Under Settings the three store tabs (Albert Heijn, Picnic, Jumbo) are now merged into one "Stores" menu with a drop-down list. The menu button stays highlighted while one of its stores is open, closes when you pick a store, click elsewhere or press Escape, and works on phones too. The settings tab bar is shorter and tidier; nothing changes in what the pages do. Texts that point to the old tabs now say "Settings > Stores > Albert Heijn" (or Picnic, Jumbo).*

### Nieuw in 2.12.0

- **Winkels onder één menu bij Beheer.** De drie winkeltabs *Albert Heijn*, *Picnic* en *Jumbo* staan niet meer los naast elkaar, maar onder één knop **Winkels** met een uitklapmenu eronder. Kies een winkel in de lijst om de koppeling, de instellingen en de verkenner van die winkel te openen. De knop **Winkels** blijft gemarkeerd zolang een van de winkels open staat; het menu klapt dicht als je een winkel kiest, ergens anders klikt of op `Esc` drukt. Het werkt ook op een telefoon.
- **Kortere tabbalk.** Onder Beheer staan nu *Winkels*, *Mealie*, *Beveiliging*, *AI-apps*, *Gebruikers* (alleen de beheerder, met inloggen aan), *Auditlog* en *Updates*. Een gewone gebruiker ziet het winkelmenu niet, net als de losse winkeltabs eerder.
- **Verwijzingen bijgewerkt.** Meldingen, de onboarding, de README's en de voorbeeld-`.env`-bestanden zeggen nu *Beheer > Winkels > Albert Heijn* (of *Picnic*, *Jumbo*) in plaats van *Beheer > Albert Heijn*.

### Goed om te weten

- Alleen de indeling van het menu verandert: de koppelingen, de MCP-tools en de API werken zoals voorheen.
- Pull requests: nog geen; deze versie volgt uit de vraag om de winkels onder Beheer samen te voegen in een menu met submenu.

## v2.11.0

*English summary: New Settings > Audit log (administrator only). It records every request MealieShopper makes to Albert Heijn, Picnic and Jumbo (method, address, status, duration, request and answer) and every request an AI assistant makes over MCP (tool, input, result), including the steps where an assistant links up. A store request made while an MCP tool runs is linked to that tool call, so you can see exactly what an assistant's question caused. Filter by store or errors, search, open an entry for details, export (JSON or CSV) or copy entries, clear the log. Passwords, tokens, codes, cookies and e-mail addresses are replaced by "[verborgen]" before storing; headers are never stored; entries are deleted after 30 days. Four optional settings: MEALIESHOPPER_AUDIT_LOG, _BODIES, _BODY_CHARS and _RETENTION_DAYS.*

### Nieuw in 2.11.0

- **Auditlog.** Onder **Beheer > Auditlog** (alleen de beheerder) staat het verkeer met Albert Heijn, Picnic en Jumbo: elk verzoek met tijd, methode en adres, uitkomst, duur, het verzoek en het antwoord. Een GraphQL-verzoek aan Jumbo of AH heet naar zijn operatie (bijvoorbeeld *GraphQL FavoriteRecipes*). Fouten en netwerkstoringen staan er ook in, en een doorverwijzing telt als één verzoek.
- **MCP-verzoeken van AI-assistenten.** Elk `initialize`, `tools/list` en `tools/call` komt in de log, met de app (bijvoorbeeld *Claude*) en gebruiker erachter, de ingevoerde argumenten en het resultaat. Ook geweigerde verzoeken (geen of ongeldig token, te groot, onbekend protocol) en de OAuth-stappen waarmee een assistent koppelt staan erin; van die stappen bewaren we nooit de tokens.
- **Wat veroorzaakte wat.** Een winkelverzoek dat tijdens een MCP-tool loopt, krijgt hetzelfde correlatie-id als die tool. Bij een MCP-verzoek zie je welke winkelverzoeken eruit volgden (of dat het antwoord uit de lokale kopie kwam), en bij een winkelverzoek door welk MCP-verzoek het kwam.
- **Filteren en zoeken.** Per winkel (Albert Heijn, Picnic, Jumbo, AI-assistenten), alleen fouten, zoeken op actie, adres of fout, en `Meer laden` voor oudere regels. Bovenaan staat per dienst het aantal verzoeken en fouten van de laatste 24 uur. Met `Log wissen` wis je alles; dat blijft zelf als regel in de log staan.
- **Exporteren en kopiëren.** `Exporteren (JSON)` en `Exporteren (CSV)` downloaden de log met de gekozen filters (hooguit de nieuwste 5000 regels, met verzoek en antwoord); `Lijst kopiëren` zet die regels als JSON op het klembord en `Kopiëren` bij een geopende regel doet dat voor die ene regel. Een export komt zelf als regel in de log. In de CSV krijgt een cel die met `=`, `+`, `-` of `@` begint een voorvoegsel, zodat een spreadsheet er geen formule van maakt.
- **Instellingen.** `MEALIESHOPPER_AUDIT_LOG` (uit met `false`), `MEALIESHOPPER_AUDIT_BODIES` (alleen wie, wat en uitkomst bewaren), `MEALIESHOPPER_AUDIT_BODY_CHARS` (standaard 4000) en `MEALIESHOPPER_AUDIT_RETENTION_DAYS` (standaard 30, `0` wist nooit). Zie de README.
- API: `GET /api/audit` (met `service`, `status=error`, `q`, `limit`, `before`), `GET /api/audit/<id>`, `GET /api/audit/export?format=json|csv` en `DELETE /api/audit`, alleen voor de beheerder.

### Goed om te weten

- **Geheimen worden niet bewaard.** Wachtwoorden, tokens, codes, cookies, sessies en e-mailadressen worden vóór het opslaan vervangen door `[verborgen]`, in het adres, het verzoek en het antwoord. Headers bewaren we niet.
- **Antwoorden kunnen persoonlijke gegevens bevatten**, zoals je bestellingen. Alleen de beheerder ziet de log, en na 30 dagen worden regels gewist. Wil je dat niet, zet dan `MEALIESHOPPER_AUDIT_BODIES=false`.
- Verkeer met Mealie en GitHub (de updatecheck) staat bewust niet in de log. Van de AH-loginproxy bewaren we alleen de pagina's, geen stijlen, scripts of inhoud.
- De log legt alleen vast: er verandert niets aan wat de koppelingen of de MCP-tools doen.

## v2.10.0

*English summary: Your own Jumbo recipe lists (such as "Kerst") are now next to Favourites under Stores > Recipes (choose Jumbo): a list picker with the recipe counts, 24 recipes per page with "More recipes", and the same "View" and "Import" buttons. They come from jumbo.com's own stored request (getRecipeListById). The MCP tools list_jumbo_recipe_lists and get_jumbo_recipe_list replace list_jumbo_favorite_recipes (get_jumbo_recipe_list without a list gives the favourites), and the explorer got a probe for an own list. Built from the real request of jumbo.com but tried only with mocked responses so far; the answer is read by its shape (recipe cards and paginationInfo), so a different nesting does no harm.*

### Nieuw in 2.10.0

- **Je eigen receptenlijstjes bij Jumbo.** Onder **Winkels > Recepten** (keuze *Jumbo*) staat nu een keuzelijst met *Favorieten* en je eigen lijstjes (zoals *Kerst*), met het aantal recepten erbij. Elk lijstje werkt als de Favorieten: per 24 met `Meer recepten`, `Bekijken` voor de ingrediënten en de bereiding, en `Importeer` in Mealie. Heb je geen eigen lijstjes of lukt het overzicht niet, dan zie je alleen je Favorieten zoals voorheen.
- **MCP.** `list_jumbo_recipe_lists` (je lijsten met aantallen) en `get_jumbo_recipe_list` (de recepten van een lijst, op naam of id; zonder opgave de Favorieten). Ze vervangen `list_jumbo_favorite_recipes` uit 2.9.0.
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de proef *een eigen receptenlijst* (aantal en vorm, geen titels).

### Goed om te weten

- Het antwoord van de lijst lezen we aan zijn vorm (de receptkaarten en `paginationInfo`), want de vorm van een eigen lijstje kennen we alleen uit de aanvraag. Klopt het totaal niet, dan telt MealieShopper wat er binnenkomt.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.9.2

*English summary: Fix: the Albert Heijn basket is now read correctly. AH nests the product in each shopping-list line (`productDetails.product`) and spells the ticked-off flag `strikedthrough`, so every line used to be skipped. The basket now shows the products with image, brand, size and price. Paging through past orders uses the confirmed row offset.*

### Opgelost in 2.9.2

- **Het AH-mandje bleef leeg.** Het rapport van **AH-routes verkennen** liet zien dat AH het product in elke regel van de boodschappenlijst onder `productDetails.product` zet en het vinkje `strikedthrough` noemt; de eerste versie zocht op een andere plek en sloeg daardoor elke regel over. Het mandje leest nu de regels goed en toont per product de afbeelding, het merk, de inhoud, de prijs, de bonus en het bedrag (aantal x prijs). Afgevinkte regels staan er nog steeds niet bij; een vrije tekstregel zonder product blijft staan.
- **Afgelopen bestellingen:** de verkenner bevestigde dat `offset` rijen telt (bij jou 21 afgeronde bestellingen). Het bladeren is daarop vereenvoudigd en de controle in de verkenner is verwijderd.

## v2.9.1

*English summary: Fix: the Albert Heijn basket in the Basket tab was empty because it showed the active web-shop order, while the weekly menu writes to your shopping list (what you see in the AH app). It now shows that shopping list and refreshes it when you open the tab.*

### Opgelost in 2.9.1

- **Het AH-mandje in het tabblad Mandje toonde niets**, ook als je producten via het weekmenu in je AH-mandje had gezet en ze in de AH-app zag. Die producten staan op je **boodschappenlijst** bij AH, terwijl het tabblad de actieve bestelling van de webwinkel las (die vaak leeg is). Het tabblad toont nu die boodschappenlijst (naam en aantal; afgevinkte regels staan er niet bij), haalt hem op zodra je het tabblad opent en bewaart een kopie, die blijft staan als AH even niet bereikbaar is.
- De tool `get_ah_basket` van de AI-assistent leest dezelfde lijst.
- **AH-routes verkennen** meldt nu ook de vorm van de boodschappenlijst. De vorm van dat antwoord is nog niet tegen een echt account gezien: staat er niets of iets geks, draai de verkenner dan en deel het rapport.

## v2.9.0

*English summary: Your favourite Jumbo recipes are now under Stores > Recipes (choose Jumbo): 24 per page with "More recipes", "View" (ingredients, method, tip, nutrition) and "Import" into Mealie. They come from jumbo.com's own stored request (FavoriteRecipes). A read-only MCP tool (list_jumbo_favorite_recipes) and an explorer probe were added. Your own recipe lists (such as "Kerst") are not shown yet. The recipe view is now shared between Find recipes and the favourites. Built from the real answer of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 2.9.0

- **Je favoriete Jumbo-recepten.** Onder **Winkels > Recepten** (keuze *Jumbo*) staan je favorieten van jumbo.com, per 24 met `Meer recepten`. `Bekijken` toont de ingrediënten, de bereiding, de tip en de voedingswaarden; `Importeer` zet het recept in Mealie. Alle Jumbo-recepten zoek je bij **Recepten zoeken**.
- **MCP.** `list_jumbo_favorite_recipes` (alleen lezen); met `get_jumbo_recipe` haalt een AI-assistent de ingrediënten en bereiding op.
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de proef *favoriete recepten* (aantallen en vorm, geen titels).

### Goed om te weten

- **Eigen receptenlijstjes ontbreken nog** (zoals *Kerst*). Het overzicht kent ze, maar de aanvraag die de recepten van zo'n lijstje geeft, is alleen bekend voor *Favorieten*.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.8.1

*English summary: Picnic orders. Under Stores > Picnic there is a new Orders tab. Upcoming deliveries are fetched live; delivered orders are synced to a local copy in MealieShopper (date, delivery window, status, total and the products with quantity, size and price), so they stay viewable without a connection to Picnic. Sync fetches the overview and the products of up to 25 orders at a time, newest first; a delivered order is never deleted again. Open an order and put all its products back in your Picnic basket with one button (an order is never placed). The MCP tools list_picnic_orders and get_picnic_order read the local copy; get_picnic_delivery fetches one delivery live. No address is stored. Note: this account has no deliveries yet (the explorer shows an empty list), so the shape of an order is built from the known Picnic API and unverified; the explorer now also reports the shape of the first delivery as soon as there is one.*

### Nieuw

- **Bestellingen van Picnic.** Onder **Winkels > Picnic** staat een tab *Bestellingen* met je aankomende bezorgingen (live bij Picnic opgehaald) en je afgeleverde bestellingen: dag, bezorgvenster, status (*Gepland of onderweg*, *Geleverd*, *Geannuleerd*) en totaalbedrag, de nieuwste eerst.
- **Afgeleverde bestellingen worden lokaal bewaard.** De knop *Synchroniseren* haalt het overzicht van je leveringen bij Picnic op en bewaart de afgeleverde bestellingen in MealieShopper, met hun producten. Daarna lees je ze uit die kopie: ook zonder verbinding met Picnic zijn ze te bekijken en te bevragen. Per keer komen de producten van hooguit 25 bestellingen binnen, de nieuwste eerst (*… bestellingen nog zonder producten: synchroniseer opnieuw*). Een afgeleverde bestelling verandert niet meer en wordt nooit verwijderd, ook niet als Picnic hem later niet meer toont; lopende en geannuleerde bestellingen bewaren we niet. Je afleveradres wordt niet bewaard. Is Picnic niet bereikbaar, dan staat bij de aankomende bestellingen een melding en blijven de bewaarde bestellingen zichtbaar.
- **De producten van een bestelling.** Open een bestelling voor de producten met aantal, inhoud en prijs. Met **Alles in mandje** zet je ze met hun aantallen opnieuw in je Picnic-mandje; er wordt nooit een bestelling geplaatst.
- **AI-assistent.** Nieuw: `list_picnic_orders` en `get_picnic_order` lezen de lokale kopie (werkt ook zonder Picnic); `get_picnic_delivery` haalt één levering live op. `list_picnic_deliveries` (al aanwezig) blijft de live lijst, ook met lopende leveringen. Alles alleen lezen en zonder adres.
- API: `GET /api/picnic/orders`, `GET /api/picnic/orders/<id>` en `POST /api/picnic/orders/sync` voor de lokale kopie; `GET /api/picnic/deliveries/<id>` geeft één levering live met haar producten; `GET /api/picnic/deliveries` is nu nieuwste eerst en geeft ook de bezorgtijd (`deliveredStart`, `deliveredEnd`).

### Verkenner

- Nieuw: het aantal leveringen in het overzicht, en, zodra er een levering is, de vorm van de eerste (`GET deliveries/<id>`), zonder waarden.

### Goed om te weten

- **Nog niet met echte gegevens geprobeerd.** Je Picnic-account heeft nog geen leveringen (het verkennerrapport toont een lege lijst), dus ik heb de vorm van een bestelling niet gezien. De lijst en het detail zijn gebouwd naar de bekende Picnic-API en lezen alles voorzichtig: ontbreekt er een veld, dan blijft dat veld leeg in plaats van dat er iets stukgaat. Heb je straks een eerste bezorging, draai dan de verkenner opnieuw en deel het rapport; het onderdeel *bestelling: GET deliveries/<id>* laat zien of de producten goed binnenkomen.
- Tot die tijd staat er *Nog niet gesynchroniseerd* of *Je hebt nog geen bestellingen bij Picnic.*
- Recepten die je met een bestelling hebt besteld (*Opnieuw bestellen* bij de recepten) volgen pas als er bestellingen zijn om dat aan te zien.

## v2.8.0

*English summary: "Find recipes" now searches Albert Heijn, Jumbo and Picnic at the same time (Jumbo and Picnic as soon as they are linked). Results are grouped per store, with "More recipes" for AH and Jumbo; a failing store leaves the others in place. For Jumbo and Picnic, "View" opens the recipe in the same tab with ingredients and method, and "Import" puts it in Mealie. Picnic has no recipe search, so MealieShopper filters your saved recipes and the cookbook by name. The separate Jumbo search under Stores > Recipes from 2.7.0 moved here, and AH results now show the result of an import under the card.*

### Verbeterd in 2.8.0

- **Recepten zoeken bij AH, Jumbo en Picnic tegelijk.** Eén zoekveld; de resultaten staan per winkel bij elkaar. Jumbo en Picnic doen mee zodra ze gekoppeld zijn. Een winkel die niet antwoordt of een fout geeft, laat de andere resultaten staan, met de melding bij die winkel.
- **Meer recepten.** In plaats van Vorige en Volgende laden AH en Jumbo de volgende pagina onder de resultaten die er al staan (`Meer recepten`).
- **Bekijken en importeren bij Jumbo en Picnic.** `Bekijken` opent het recept in hetzelfde tabblad met ingrediënten, bereiding en (bij Jumbo) tip en voedingswaarden; `Importeer` zet het in Mealie.
- **Picnic doorzoeken op naam.** Picnic heeft geen zoekfunctie voor recepten; MealieShopper filtert je bewaarde recepten en het kookboek op de woorden uit je zoekopdracht (accenten en hoofdletters tellen niet).
- **Het Jumbo-zoekveld uit 2.7.0 is verplaatst** van Winkels > Recepten naar Recepten zoeken. Bij AH verschijnt het resultaat van een import nu onder de kaart.

## v2.7.0

*English summary: Jumbo recipes are in MealieShopper. Under Stores > Recipes (choose Jumbo) you search all recipes of jumbo.com, open one to see the ingredients, method, tip and nutrition values, and import it into Mealie as a schema.org recipe (with the jumbo.com link as its source). Pasting a jumbo.com recipe link into the importer also goes through the Jumbo API when Jumbo is linked. Two read-only MCP tools (search_jumbo_recipes, get_jumbo_recipe) and explorer probes were added. Your own recipe lists (Favourites and custom lists) are not shown yet: Jumbo's request that returns the recipes of one list is still needed. Built from the real answers of jumbo.com (getRecipe, getRecipesListers, getRecipeLists) but tried only with mocked responses so far.*

### Nieuw in 2.7.0

- **Recepten van Jumbo.** Onder **Winkels > Recepten** (keuze *Jumbo*) zoek je in alle recepten van jumbo.com, per 24 met `Meer recepten`. `Bekijken` toont de ingrediënten (met de hoeveelheden van het recept), de bereiding, de tip, de voedingswaarden en een link naar het recept op jumbo.com.
- **Importeren in Mealie.** `Importeer` zet het recept in Mealie als schema.org-recept met de link naar jumbo.com als bron, dus Mealie hoeft de Jumbo-pagina niet op te halen (jumbo.com weert servers). Een receptlink van jumbo.com (`jumbo.com/recepten/...`) in de importfunctie gaat, als Jumbo gekoppeld is, ook via de Jumbo-API. Receptnummers met een streepje (`1416890-7`, van Smulweb) werken ook.
- **MCP.** `search_jumbo_recipes` en `get_jumbo_recipe` (alleen lezen).
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook het zoeken, je receptenlijsten, één recept en recepten op id (alleen vormen en aantallen, geen titels).

### Goed om te weten

- **Je receptenlijsten ontbreken nog.** Het overzicht van de site (`getRecipeLists`) kent je lijsten (Favorieten en eigen lijstjes) met hun aantallen, maar toont per lijst alleen titel en plaatje. De recepten van één lijst komen uit een andere aanvraag, waarvan de opgeslagen aanvraag (`operationName`, hash en variabelen) nog nodig is. Bladeren per categorie volgt ook daarna.
- Alleen getest met nagebootste antwoorden in de vorm van de echte.

## v2.6.2

*English summary: Picnic favorites now show only your own favorites. The explorer report, with two products hearted in the Picnic app, showed how the app knows: the Favorites tab lists suggested products under a "Favorieten" heading, and the product page of a favorite carries `likedSellables: {["s…"]: true}` in the code of its heart button (false for the others). MealieShopper now takes the products under the "Favorieten" heading and keeps those whose product page says true. That is one request per product (at most 30), done lazily: the app checks a few at a time and shows the favorites it has found so far, and the server remembers each product's answer for 30 minutes (the Load button forgets it). If Picnic says nothing, the list stays empty with an explanation, as before.*

### Nieuw

- **Favorieten van Picnic: alleen je eigen favorieten.** Het verkennerrapport met twee favoriete producten liet zien hoe de app het weet: de tab *Favorieten* toont voorgestelde producten onder de kop *Favorieten* en een aantal gangpaden, en op de productpagina van een favoriet zet Picnic in de code van de hartjesknop `likedSellables: {["s…"]: true}` (bij de andere producten `false`). In het rapport waren dat precies de eerste twee producten. MealieShopper neemt nu de producten onder de kop *Favorieten* (17 in het rapport) en houdt over wat zijn productpagina als favoriet markeert.
- Dat kost één verzoek per product (hooguit 30), maar **lui en met geheugen**: de app controleert er een paar tegelijk en toont onderweg de favorieten die al gevonden zijn (*Nog 12 producten controleren…*). De server onthoudt per product 30 minuten wat hij wist, en de pagina van de tab twee minuten, dus de volgende keer gaat het direct. De knop *Laden* vergeet dat geheugen en controleert alles opnieuw; zet je in de Picnic-app een hartje, gebruik dan die knop. Een product dat niet laadt wordt overgeslagen en pas na twee minuten opnieuw geprobeerd; is de koppeling verlopen, dan zie je die melding.
- Zegt Picnic niets over favorieten, dan blijft de lijst leeg met de uitleg uit 2.4.3, in plaats van alle producten van de pagina. Zijn er geen favorieten, dan staat er *Je Picnic-favorieten zijn leeg.*
- `list_picnic_favorites` (MCP) gebruikt dezelfde lijst.

### Goed om te weten

- **Alleen wat onder de kop *Favorieten* staat wordt gecontroleerd.** Heb je meer favorieten dan Picnic daar toont (in het rapport 17 plekken), dan missen er mogelijk een paar.
- Zoeken in de favorieten werkt zoals eerder; de knop *In mandje* zet het product in je Picnic-mandje.
- *Opnieuw bestellen* bij de recepten is nog leeg: je account heeft nog niets laten bezorgen.

## v2.6.1

*English summary: The explorer report found how the Picnic app changes a favorite: `POST pages/task/toggle-sellable-favorite` with `{"payload": {"sellable_id": "s…", "status": "UNLIKE"}}`, after which the app reloads the "zero order" page of the Favorites tab. So that page does change with your favorites. Reading them is still open: the product page's heart starts from a state the app fills itself. The explorer now reports the outline of the Favorites page (headings and runs of product tiles), the pages it loads lazily, and the true/false values the server puts in the heart code of a handful of product pages, to see which one marks your favorites. No behaviour change in the app.*

### Verkenner

- Uit het rapport: een favoriet weghalen gaat met **`POST pages/task/toggle-sellable-favorite`** met `{"payload": {"sellable_id": "s…", "status": "UNLIKE"}}` (waarschijnlijk `LIKE` om er een toe te voegen). Daarna laadt de app de pagina `purchase-page-…-zero-order-targeted` opnieuw: die pagina verandert dus mee met je favorieten.
- `/bootstrap` heeft naast de tabs geen favorieten (alleen instellingen), en alle gegokte routes geven 404.
- Nieuw: de opbouw van de pagina van de tab Favorieten in leesvolgorde: koppen en groepen producttegels (`kop: …`, `tegels: 12`). Zo zien we of de favorieten een eigen kopje hebben en hoeveel producten eronder staan. Alleen korte teksten van onderdelen zonder producten komen erin, nooit productnamen.
- Nieuw: de pagina's die de Favorieten-pagina zelf later laadt (`pageConfig`), met hun opbouw en de markering `saved`.
- Nieuw: de waar/onwaar-waarden die de server in de hartjescode van de productpagina's van acht producten zet. Verschillen die per product, dan is dat de markering van je favorieten.

### Goed om te weten

- De app zelf verandert niet; de Favorieten-tab toont nog de uitleg uit 2.4.3.
- De verkenner roept `toggle-sellable-favorite` niet aan: alleen lezen.

## v2.6.0

*English summary: There is now a dark/light mode button next to the language flags (top right, on the login page and in the setup guide). The choice is kept in your browser; without a choice MealieShopper follows your device setting, also when that changes. The AI consent page follows the device setting too.*

### Nieuw in 2.6.0

- **Donkere en lichte modus.** Naast de vlaggetjes staat een knop (een maan in het lichte thema, een zon in het donkere) waarmee je wisselt. Hij staat overal waar de vlaggen staan: bovenaan de app, op de inlogpagina, bij een uitnodiging en in de installatiehulp. De keuze blijft in je browser bewaard (`ms-theme`); zonder keuze volgt MealieShopper de instelling van je apparaat, ook als die tijdens het gebruik wisselt. Het thema wordt al in de kop van de pagina gezet, zodat een donker scherm niet eerst licht oplicht. De themakleur van de browser (`theme-color`) wisselt mee.
- **Donker thema in de huisstijl:** basilicumgroen blijft het accent (lichter op donker, met donkere tekst op groene knoppen), meldingen houden hun kleuren en de onderste menubalk in de geinstalleerde app krijgt een donkere variant. De kassabon en de QR-code blijven licht, zoals op papier.
- **Toestemmingsscherm voor AI-apps** (OAuth) volgt de instelling van je apparaat.

## v2.5.2

*English summary: The last explorer report shows where the favorites are NOT: the Favorites tab of an account without deliveries is a "zero order" page of suggested products (`purchase-page-…-zero-order-targeted`), and the heart on a product reads `getGlobalState("sellable-favorites").likedSellables`, a state the app fills itself, not something in the page. The explorer now reports the full code of the heart button, the pages that load lazily, the fields of `/bootstrap` next to the tabs, the "remove favorite" dialog with the right parameters and a handful of guessed favorites routes, to find what fills that state. No behaviour change in the app.*

### Verkenner

- Uit het rapport: de tab *Favorieten* van een account zonder leveringen toont een pagina met voorgestelde producten (`purchase-page-…-zero-order-targeted`); daar staat niets over jouw favorieten op. Het hartje bij een product leest `getGlobalState("sellable-favorites").likedSellables`: een toestand die de app zelf vult. De pagina's bevatten dus geen favorieten; de app haalt ze ergens anders.
- Nieuw: de volledige code van de hartjesknop op de productpagina (met getallen en id's vervangen door `<id>`), zodat te zien is of er een route of actie achter zit.
- Nieuw: welke pagina's er later nog worden geladen (`pageConfig`: pagina en namen van de parameters).
- Nieuw: de velden van `/bootstrap` naast de tabs (alleen de vorm), in de hoop dat de toestand `sellable-favorites` daar zit.
- Nieuw: de bevestiging bij een favoriet weghalen met parameters als zoekopdracht (de vorige keer was het een 404 omdat de parameters in de verkeerde vorm stonden), en een paar gokken voor favorietenroutes (`/favorites`, `/sellable-favorites`, …).
- *Opnieuw bestellen* is leeg omdat het account nog niets heeft laten bezorgen (`purchases-meals-plan-item-empty`).

### Goed om te weten

- De app zelf verandert niet. Zonder de bron van `sellable-favorites` blijft de lijst op de Favorieten-tab leeg met een uitleg.
- De code van de hartjesknop is Picnics eigen code en kan in uitzonderlijke gevallen de naam van een product bevatten (een melding als *toegevoegd aan favorieten*); persoonlijke gegevens staan er niet in.

## v2.5.1

*English summary: The explorer report shows this account has 0 completed deliveries, so the Favorites tab points to the "no purchases" page, whose `saved` field is empty and the page `purchases-page-root` only has two products; none of them says which products you favorited. The explorer now reports how those pages (and the product page and the "remove favorite" dialog) fetch their data: addresses, requests, waiting components and state, with numbers and ids redacted, so the real favorites source can be found. No behaviour change in the app.*

### Verkenner

- Uit het rapport: dit account heeft **0 afgeronde leveringen**. Daarom wijst de tab *Favorieten* naar de pagina voor een account zonder aankopen, en bevatten de pagina's `purchases-page-root` (2 producten, de rest laadt later) en `purchases_page_no_consent_root` geen markering van favorieten. Het account heeft wel de functies `MANUAL_FAVORITES` en `PPP_PURCHASES_TAB`.
- Nieuw: per pagina (`purchases_page_no_products_root`, `purchases-page-root`, `purchased-deep-dive-page-content`, `meals-purchase-page-root`, de bevestiging bij een favoriet weghalen en een productpagina) hoe die pagina haar gegevens ophaalt: adressen en verzoeken, wachtonderdelen (`SUSPENSE`), de toestand (`state`) en stukjes code over favorieten of opslaan. Getallen en id's zijn vervangen door `<id>`; er komen geen producten of teksten in.
- *Opnieuw bestellen* is leeg omdat het account nog niets heeft laten bezorgen; dat is waarschijnlijk geen fout.

### Goed om te weten

- De app zelf verandert niet; dit is weer een verkenstap om te zien waar Picnic de handmatige favorieten vandaan haalt.

## v2.5.0

*English summary: All your past Albert Heijn orders are now fetched, not just the latest 10: MealieShopper pages back through them. The products of delivered orders are read correctly (AH's "amount" is a quantity, so the line amount is quantity times price, which also fixes orders already stored). Cancelled orders no longer show up as open orders.*

### Nieuw in 2.5.0

- **Al je afgelopen bestellingen**, niet alleen de laatste 10. Uit het rapport van **AH-routes verkennen** bleek dat AH het argument `offset` kent; MealieShopper bladert nu door de afgeronde bestellingen (10 per pagina, nieuwste eerst) tot de oudste. Of `offset` rijen of pagina's telt weten we nog niet zeker; het ophalen probeert beide en neemt wat werkt. Een volgende synchronisatie stopt zodra ze bij bestellingen komt die al bewaard zijn. De producten van maximaal 25 bestellingen komen per keer binnen; staat er "nog zonder producten", synchroniseer dan opnieuw.
- **Geannuleerde bestellingen** komen soms ook bij de open bestellingen voorbij. Die worden nu overgeslagen, en een bestelling die je eerder als open had en later is geannuleerd, wordt verwijderd.
- **Bedragen van productregels kloppen.** Bij AH is `amount` in een bestelling een aantal, geen bedrag. Het bedrag van een regel is nu aantal x prijs. Dat geldt ook voor bestellingen die al waren bewaard: het wordt bij het tonen uitgerekend, je hoeft niets opnieuw op te halen.
- **Verkenner:** de introspectie (staat bij AH uit) en de pagineringsgokken zijn vervangen door een controle hoe `offset` telt, met alleen aantallen en geen id's.

## v2.4.3

*English summary: Picnic favorites no longer fall back to listing every product on the page. The explorer report showed that the Favorites tab of this account points to the "no purchases" page (`purchases_page_no_products_root`): 183 products and a `saved` field that is empty, so there is nothing that says which products are yours. Without a marker MealieShopper now shows no products and says why, and it also tries the `purchases-page-root` page. The explorer gets probes for the purchases pages, the product page's favorite fields and the account's delivery count and purchases consent, so the real source of your favorites can be found next.*

### Opgelost in 2.4.3

- **Favorieten van Picnic toonden nog steeds alle producten.** Het verkennerrapport laat zien waarom: de tab *Favorieten* van dit account wijst naar `purchases_page_no_products_root`, de pagina voor een account zonder aankopen. Daarop staan 183 producten en één leeg veld `saved`; er is dus niets wat zegt welke producten van jou zijn. Zonder markering toont MealieShopper nu **geen producten** maar de melding *Picnic geeft in de app-gegevens niet door welke producten je als favoriet hebt opgeslagen…*, in plaats van de hele pagina als favorieten te presenteren. `list_picnic_favorites` (MCP) geeft dan een lege lijst met `onlyFavorites: false`.
- **Ook de pagina `purchases-page-root` wordt geprobeerd** als de pagina van de tab niets markeert. Markeert die wel producten, dan komen alleen die in de lijst.

### Verkenner

- Nieuw: de pagina's `purchases-page-root` (met en zonder parameters) en `purchases_page_no_consent_root`, met per pagina de markering `saved`.
- Nieuw: welke velden over favorieten er op de productpagina's van de eerste drie producten staan, met hun waarde (alleen waar/onwaar/getal, geen teksten).
- Nieuw: het aantal afgeronde leveringen en de toestemming voor aankopen van je account (alleen een getal en waar/onwaar), plus de namen van functies met `favoriet`, `purchase` of `saved` erin. Daaruit blijkt of Picnic je favorieten voor dit account op een andere plek bewaart.

### Goed om te weten

- **Je favorieten staan er nog niet.** Dit is een tussenstap: eerst niet meer iets verkeerds tonen. Deel na het bijwerken het verkennerrapport; de nieuwe onderdelen *favorieten: …* laten zien waar Picnic je echte favorieten bewaart.

## v2.4.2

*English summary: Fixes "The update check failed (HTTP 403 (rate limit))". The check no longer uses api.github.com, where anonymous requests are limited to 60 per hour per IP address (easily used up on a shared address), but the public releases page, which GitHub redirects to the latest release tag. Nothing else changes.*

### Opgelost in 2.4.2

- **Updatecheck: `HTTP 403 (rate limit)`.** GitHub beperkt anonieme aanvragen naar `api.github.com` tot 60 per uur per IP-adres; op een gedeeld adres was die limiet al op, en dan faalde de updatecheck. De check kijkt nu op `github.com/helmerzNL/MealieShopper/releases/latest`, dat GitHub doorverwijst naar de nieuwste release-tag. Die pagina valt niet onder de API-limiet. Er verandert verder niets: nog steeds hooguit om de zes uur, zonder informatie over jou of de installatie.

## v2.4.1

*English summary: Fixes the Picnic favorites showing all products instead of only your favorites. The Favorites tab of the Picnic app lists far more products than the ones you saved (197 in the explorer report), and the page marks yours with a `saved` field. MealieShopper now keeps only the products Picnic marks as saved, and when the page does not say which are favorites it shows all of them with a notice instead of silently pretending. The explorer reports how that field looks. The MCP tool list_picnic_favorites says whether it filtered.*

### Opgelost in 2.4.1

- **Favorieten van Picnic toonden alle producten.** De Favorieten-tab van de Picnic-app wijst naar een pagina met veel meer producten dan je eigen favorieten (197 in het verkennerrapport). MealieShopper nam ze allemaal mee. Op die pagina staat een veld `saved` dat markeert welke producten je hebt opgeslagen; nu blijven alleen die over. Dat kan een waar/onwaar-vlag bij elk product zijn of een lijst met id's van opgeslagen producten.
- **Geen stil gokken.** Zegt de pagina niet welke producten favoriet zijn, dan staan alle producten van de pagina in de lijst, met de melding *Picnic geeft niet aan welke producten je als favoriet hebt opgeslagen, dus dit zijn alle … producten van de pagina*. Zijn er markeringen maar is er geen enkele favoriet, dan is de lijst leeg. Het antwoord van `GET /api/picnic/favorites` bevat nu ook `total` en `filtered`; `list_picnic_favorites` (MCP) meldt met `onlyFavorites` of er is gefilterd.
- **De verkenner meldt hoe de markering eruitziet:** de vorm van `saved`, hoe vaak het veld voorkomt en hoeveel producten we als favoriet, niet favoriet of onbekend zien. Zo is te zien of het filteren klopt.

### Goed om te weten

- **De markering is afgeleid uit alleen een veldnaam** (`saved`, uit het vorige rapport); de waarden heb ik niet gezien. Staat er na het bijwerken nog steeds een lijst met alle producten, of een lege lijst terwijl je wel favorieten hebt, deel dan het verkennerrapport: het nieuwe onderdeel *de markering `saved`* laat zien hoe Picnic het opslaat.
- De Favorieten-tab van jouw app wijst naar `purchases_page_no_products_root`. Het kan zijn dat Picnic voor accounts zonder afgeronde bestellingen een andere pagina toont dan voor accounts met bestellingen.

## v2.4.0

*English summary: Your Albert Heijn orders are back. Under Stores > Orders you see your upcoming orders (still open, with their delivery time) and your past orders (delivered, kept for good), next to your receipts, and the order basket is back under the Basket tab. The "coming soon" lines are gone.*

### Nieuw in 2.4.0

- **Bestellingen bij Albert Heijn zijn terug.** Onder **Winkels > Bestellingen** staan je **aankomende bestellingen** (nog open, met bezorgmoment en totaal, de eerstvolgende eerst) en je **afgelopen bestellingen** (geleverd, de nieuwste eerst), boven je kassabonnen. Open een bestelling voor de producten. Wat is afgeleverd blijft voor altijd bewaard en geannuleerde bestellingen worden overgeslagen.
- **Het AH-bestelmandje is terug** onder het tabblad **Mandje**, met een knop om te synchroniseren. Is er geen actieve bestelling, dan is het mandje gewoon leeg.
- Het synchroniseer-icoon haalt weer in één keer het mandje, de open en afgeronde bestellingen en de kassabonnen op; een deel dat mislukt stopt de rest niet. De producten van maximaal 15 bestellingen komen per keer binnen (staat er "nog zonder producten", synchroniseer dan opnieuw).
- Het zoekveld zoekt weer ook in de producten van je bestellingen, naast de kassabonnen.
- De "komen binnenkort"-regels zijn weggehaald en `ORDERS_COMING_SOON` bestaat niet meer.
- AH geeft per keer de laatste 10 afgeronde bestellingen. Hoe je verder terug komt, zoekt **AH-routes verkennen** (Beheer > Albert Heijn) uit; draai die en deel het rapport.

## v2.3.0

*English summary: Picnic is now a choice in the store list under Stores, next to Albert Heijn and Jumbo. Choosing Picnic shows two tabs: Recipes (the Picnic recipes from v2.2.0, no longer a separate tab) and the new Favorites, the products from the Favorites tab of the Picnic app, each with a button to put it in your Picnic basket. MealieShopper finds the favorites page itself through the tab bar that Picnic sends to its app (/bootstrap). A read-only MCP tool, list_picnic_favorites, shows them to your AI assistant. The explorer now reports that tab bar. Not tried against a real account yet.*

### Nieuw in 2.3.0

- **Picnic in de keuzelijst bij Winkels.** Naast **Albert Heijn** en **Jumbo** staat nu **Picnic** (zodra het gekoppeld is). Kies je Picnic, dan blijven er twee tabs over: **Recepten** (de Picnic-recepten uit 2.2.0, niet meer een los tabblad) en **Favorieten**. Lijstjes en Bestellingen horen bij AH en Jumbo en verdwijnen bij Picnic; wissel je terug, dan komen de tabs van die winkel terug.
- **Favorieten van Picnic.** De producten uit de tab Favorieten van de Picnic-app, met naam, inhoud, prijs en een aanbiedingslabel, en een knop **In mandje** die er één in je Picnic-mandje zet (er wordt niets besteld). Zoeken op naam kan ook. Nieuwe route: `GET /api/picnic/favorites`.
- **Zelf de favorietenpagina vinden.** Geen enkele library kent de pagina, maar Picnic stuurt zijn app de tabbalk (`/bootstrap`) met per tab de pagina waar hij naartoe gaat. MealieShopper zoekt de tab die over favorieten gaat en haalt die pagina op. Staan er op die pagina zelf geen producten, dan volgt MealieShopper de pagina's waarnaar hij verwijst (maximaal vier), omdat de inhoud van een tab bij Picnic vaak op een aparte pagina staat.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_favorites` (naam, inhoud, prijs en aanbieding). Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **De verkenner meldt de tabbalk** van de app (id, soort, titel, label en pagina per tab) en welke pagina als Favorieten is gekozen, met de vorm van die pagina. Zo is te zien waarom de favorieten leeg blijven als dat gebeurt.

### Goed om te weten

- **Nog niet tegen een echt account geprobeerd.** Het zoeken van de Favorieten-tab op naam (*favoriet*/*favorite* in de id, titel, label of pagina) is een aanname. Blijft de lijst leeg of staat er een melding, deel dan het verkennerrapport: daarin staat de hele tabbalk.
- Antwoordt Picnic voor een pagina met tekst in plaats van JSON, dan kan MealieShopper de favorieten niet lezen en meldt dat.

## v2.2.0

*English summary: Your Picnic recipes are now in the app. Under Stores there is a new Picnic recipes tab with the lists Saved (Cook > Saved), Order again and the Cookbook, as cards like the AH recipes. Opening a recipe shows the ingredients and the preparation, and an Import button puts the recipe in Mealie. The cookbook names are read from the tiles (the explorer showed they have no name in their data), and the route explorer now measures what is on the pages that were empty and looks for the favorites page through field names and ids, without any texts. The favorite products are not found yet.*

### Nieuw in 2.2.0

- **Picnic-recepten in de app.** Onder **Winkels** staat een tabblad **Picnic-recepten** zodra Picnic gekoppeld is. Kies de lijst: **Bewaard** (Koken > Bewaard in de Picnic-app), **Opnieuw bestellen** (recepten die je bestelde) of het **Kookboek**. De recepten staan als kaarten, net als de AH-recepten, met afbeelding, kooktijd en labels, en je kunt zoeken op naam.
- **Een recept openen** toont de ingrediënten (met de hoeveelheid die het recept nodig heeft, bijvoorbeeld *150 g Rode uien*) en de bereidingsstappen, gelezen uit de receptpagina van Picnic. Nieuwe route: `GET /api/picnic/recipes/<id>`.
- **Importeren in Mealie.** Elke kaart en elk geopend recept heeft een knop **Importeer**. MealieShopper stuurt het recept als schema.org Recipe naar Mealie (net als bij de AH-recepten), met naam, afbeelding, porties, tijd, ingrediënten en stappen; Picnic-recepten hebben geen webpagina, dus als bron staat de site van Picnic. Nieuwe route: `POST /api/picnic/recipes/<id>/import`. Is Mealie niet gekoppeld, dan staat er wat je moet doen.
- **Kookboek en Opnieuw bestellen lezen.** De tegels van het kookboek hebben geen naam in hun gegevens (de verkenner liet het zien); de naam wordt nu uit de tekst van de tegel gelezen. `list_picnic_recipes` (MCP) kent nu ook de bron `ordered` en meldt kooktijd en labels.
- **De verkenner zoekt de favorieten.** Hij telt wat er op de lege pagina's staat (soorten onderdelen), zoekt op de zoek-, product- en receptpagina's naar veldnamen en ids rond *favoriet* (alleen namen, nooit teksten van producten of recepten) en probeert nog zeven paginanamen.

### Goed om te weten

- **Nog niet tegen een echt account geprobeerd.** De receptpagina's worden gelezen zoals de community-library `picnic-api` dat doet en getest met nagebootste pagina's in de vorm uit jouw verkennerrapport. Staan er namen of ingrediënten verkeerd, deel dan het verkennerrapport; de ingrediënten en stappen staan op vaste plekken in de pagina die nog niet met een echt recept zijn bevestigd. De afbeelding van een recept staat in het formaat *medium* (kaart) of *large* (recept).
- **Opnieuw bestellen** gaf in het vorige rapport een pagina zonder receptonderdelen; of dat aan de pagina of aan je account ligt, laat de nieuwe telling in het rapport zien.
- **De favoriete producten** (de tab Favorieten in de Picnic-app) staan er nog niet in: de pagina is nog niet gevonden.

## v2.1.1

*English summary: The products of a Jumbo order should now come in. Jumbo's own error message from 1.14.6 showed that the order query expects the order number as a Float (`Variable "$orderId" of type "String!" used in position expecting type "Float!"`), so MealieShopper now asks for it that way first (a plain number). The other variants stay as a fallback. Tried only with mocked responses so far: sync again under Stores > Orders > Jumbo.*

### Opgelost in 2.1.1

- **Producten van Jumbo-bestellingen.** De foutmelding uit 1.14.6 liet zien dat Jumbo bij `order(orderId: …)` een `Float!` verwacht (*Variable "$orderId" of type "String!" used in position expecting type "Float!"*). Dat type en de naam `orderId` bleken dus goed, alleen het type niet. MealieShopper vraagt de bestelling nu eerst met een `Float!` (het nummer als gewoon getal); de andere varianten blijven als terugval. Synchroniseer opnieuw onder **Winkels > Bestellingen > Jumbo**; per keer komen de producten van de 25 nieuwste bestellingen die ze nog missen binnen.

## v2.1.0

*English summary: MealieShopper can now read your Picnic recipes: the ones you saved in the Picnic app and the cookbook, with name and id. For now they are available through a new read-only MCP tool, `list_picnic_recipes`, and the route `GET /api/picnic/recipes`; nothing is imported into Mealie and the app has no recipe screen for them yet, because the explorer report did not show the ingredients yet. The route explorer now also reports where the names, ingredients and images sit in a recipe and tries more pages for the favorites.*

### Nieuw in 2.1.0

- **Picnic-recepten lezen.** De recepten die je in de Picnic-app hebt opgeslagen en het kookboek van Picnic, met naam en id. Dit is gebouwd op de vorm die de verkenner bij jouw account vond: elk receptonderdeel op een pagina heeft `recipe_id` en `recipe_name` in zijn analysegegevens. Nieuwe route: `GET /api/picnic/recipes?source=saved|cookbook`.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_recipes` (`source`: `saved` of `cookbook`, en `limit`). Je AI-assistent kan zo je Picnic-recepten bekijken. Er wordt niets opgeslagen of aan je mandje toegevoegd. Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **De verkenner vertelt meer over recepten.** Hij meldt per receptpagina welke velden de analysegegevens van een receptonderdeel hebben, welke soorten onderdelen erin zitten (en welke velden die hebben, zonder inhoud), en telt de producttegels op elke pagina. Zo zien we waar de ingrediënten, de bereiding en de afbeelding staan.
- **Meer pagina's voor de favorieten.** Nog eens vijftien waarschijnlijke paginanamen worden geprobeerd. Uit het vorige rapport bleek dat `purchased-deep-dive-page-content` wel bestaat; het nieuwe rapport laat zien of dat een lijst producten is.

### Goed om te weten

- **De recepten staan nog niet in het scherm van de app** en worden nog niet in Mealie geïmporteerd: het rapport liet de namen en id's zien, maar nog niet de ingrediënten en de bereiding van een recept. Die komen na het volgende rapport van de verkenner.
- **De favorieten zijn nog niet gevonden.** Geen enkele library kent de pagina en de gokken uit het vorige rapport gaven een 404. Weet je waar in de Picnic-app je Favorieten staan (welk scherm of tabblad)? Dat helpt bij het zoeken.

## v2.0.0

*English summary: The container image is now built for both linux/amd64 and linux/arm64. Before, only amd64 was published, so on an ARM NAS (Synology, Raspberry Pi) the container failed to start with "exec gunicorn failed: Exec format error". Pull the image again to get the right architecture. The image is now also tagged with its version (for example 2.0.0). This is a major version number because the published image changes, not because anything in the app breaks.*

### Opgelost in 2.0.0

- **De image start nu ook op een ARM-NAS.** Op een Synology (of een Raspberry Pi) stopte de container direct met `[FATAL tini] exec gunicorn failed: Exec format error`, omdat de image alleen voor `linux/amd64` werd gebouwd. De workflow `.github/workflows/docker.yml` bouwt en publiceert nu een image voor `linux/amd64` én `linux/arm64`, onder dezelfde tag (`latest`). Docker kiest zelf de juiste architectuur.
- **De image heeft nu ook een versietag.** Naast `latest` en de commit krijgt de image de tag uit `VERSION` (bijvoorbeeld `ghcr.io/helmerznl/mealieshopper:2.0.0`), zodat je een vaste versie kunt vastzetten.

### Goed om te weten

- Draai je MealieShopper op een ARM-NAS? Haal de image opnieuw op (`docker compose pull && docker compose up -d`, of in Container Manager de image bijwerken) zodat je de arm64-versie krijgt.
- Het nieuwe hoofdnummer (2.0.0) komt doordat de gepubliceerde image verandert; in de app zelf is niets wat breekt of wat je moet aanpassen.
- De build duurt langer, omdat de arm64-versie op een amd64-server wordt nagebootst (QEMU). Er verandert niets aan de app zelf.

## v1.15.0

*English summary: Two things for Picnic. The basket now adds up: a discounted line shows the price without discount struck through and what you pay, products that are not available right now are marked with a notice, and the plus button and quantity field stop at the maximum Picnic allows per product. And the route explorer now also checks Picnic's recipe pages (saved recipes, the cookbook, the recipes home and a recipe's details) and tries a list of likely pages for the favorites; that is the first step toward reading your Picnic recipes and favorites into MealieShopper, so nothing is imported yet. Errors in the explorer report now show only the status and error code.*

### Nieuw in 1.15.0

- **Picnic-routes verkennen kijkt naar recepten.** De verkenner roept de receptpagina's van Picnic aan (opgeslagen recepten, kookboek, receptenstartpagina en de details van één recept, zoals de community-libraries `picnic-api` en `mcp-de-picnic` ze kennen) en meldt hoeveel receptonderdelen erop staan, welke velden zo'n onderdeel heeft en naar welke pagina's de pagina verwijst, zonder namen of id's. Is een antwoord geen JSON, dan meldt het rapport dat en de grootte.
- **Zoeken naar de favorieten.** Picnic heeft een favorietenpagina, maar geen library noemt hem. De verkenner probeert een lijst waarschijnlijke paginanamen; een pagina die bestaat wordt met zijn vorm en verwijzingen gemeld, de rest geeft een 404.
- Nog **niets wordt ingelezen**: eerst moet de vorm van die pagina's bekend zijn. Draai de verkenner (Beheer > Picnic) en deel het rapport; daarmee bouwen we het inlezen van je recepten en favorieten.

### Opgelost in 1.15.0

- **Regelprijzen in het Picnic-mandje tellen op tot het totaal.** Bij een aanbieding of lidmaatschapskorting stond de prijs zonder korting, waardoor de regels meer opleverden dan je totaal (bij het testmandje €35,90 tegen €35,03). Nu staat bij zo'n regel de prijs zonder korting doorgestreept en daarachter wat je betaalt. Dit geldt ook voor `get_picnic_basket` (veld `price`, en `listPrice` als er korting is).
- **Waarschuwing bij niet-beschikbare producten.** Picnic meldt per product of het nu leverbaar is. Een product dat dat niet is, krijgt *Niet beschikbaar* achter de naam, en boven het mandje staat dat zulke producten niet worden bezorgd. Ook in `get_picnic_basket` (veld `available`).
- **Maximum per product.** De plus-knop en het aantalveld stoppen bij het maximum dat Picnic per bestelling toestaat en melden het als je er overheen typt. Zet je het via `PUT /api/picnic/cart/items/<product>` toch hoger, dan weigert MealieShopper dat voordat er iets verandert. Veld `maxCount`.

### Goed om te weten

- De kortingsprijs is afgeleid uit de vorm van een echt gevuld mandje: Picnic geeft twee bedragen per regel en het lagere is wat je betaalt. Controleer na het bijwerken dat de regels optellen tot het totaal. Waarom een product niet beschikbaar is, meldt Picnic niet in het mandje.
- In het verkennerrapport staat bij een mislukte route nu alleen de statuscode en de foutcode (bijvoorbeeld `404 NOT_FOUND`), niet meer de tekst van Picnic zelf. Bij alle verkenners tonen de velden `type`, `schema`, `component` en `actionType` hun waarde: dat zijn vaste namen van onderdelen, geen persoonlijke gegevens.

## v1.14.6

*English summary: When Jumbo refuses the requests for the products of an order, the sync message now says what Jumbo answered (with order numbers and ids removed) instead of only "RuntimeError", and the explorer shows the same. This is a diagnostic step: the products of an order still need Jumbo's own stored request (operationName, hash, variables) from the browser's Network tab.*

### Verbeterd in 1.14.6

- **Duidelijke reden als de producten van Jumbo-bestellingen niet binnenkomen.** De melding na het synchroniseren zei alleen `RuntimeError`. Nu staat erin wat Jumbo antwoordde op de acht varianten van de aanvraag (bijvoorbeeld de HTTP-status en de foutmelding), met bestelnummers en id's weggehaald. **Jumbo-routes verkennen** toont dezelfde tekst bij de proef *een bestelling met producten*. De lijst met bestellingen werkt al; de producten wachten tot de opgeslagen aanvraag van de bestelpagina bekend is (`operationName`, hash en variabelen uit het tabblad Netwerk).

## v1.14.5

*English summary: When the update check fails, the Updates tab and the server log now say why (for example "HTTP 403 (rate limit)", "Timeout" or "ConnectionError") instead of only "failed".*

### Verbeterd in 1.14.5

- **Updatecheck: de oorzaak van een fout.** Mislukt de updatecheck, dan staat er nu bij Beheer > Updates ook waarom (bijvoorbeeld `HTTP 403 (rate limit)`, `Timeout`, `ConnectionError` of `SSLError`), en de serverlog bevat de volledige melding. Zo zie je of de server GitHub (`api.github.com`) niet kan bereiken of dat GitHub het verzoek weigert.

## v1.14.0

*English summary: Your completed Jumbo orders are now in MealieShopper under Stores > Orders (choose Jumbo): date, delivery time, total and the products, kept as a local copy, with a search over everything you bought and two read-only MCP tools (list_jumbo_orders, get_jumbo_order). The order list comes from jumbo.com's own stored request (OnlineOrdersAndStoreReceipts); the products of one order are requested with ordinary queries built from the field names of the website's answer, because the stored request for the order detail is not known yet. If Jumbo refuses those, the list still syncs and the sync reports why, and the explorer shows which variant worked. Your delivery address, customer number and reservation number are never stored. Store receipts and running orders are not shown for Jumbo yet. Built from the real answers of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 1.14.0

- **Je Jumbo-bestellingen.** Onder **Winkels > Bestellingen** (keuze *Jumbo*) staan je afgeronde bestellingen met datum, bezorgtijd, totaal en producten (naam, merk, pakmaat, aantal, bedrag, aanbieding en categorie). Synchroniseren haalt de lijst op (nieuwste eerst, per 10) en daarna de producten van maximaal 25 bestellingen die ze nog missen; herhaal het tot niets meer ontbreekt. Een afgeronde bestelling blijft bewaard, ook als Jumbo hem later niet meer toont; geannuleerde bestellingen bewaart MealieShopper niet. Producten die Jumbo niet meer kent (`__MISSING_PRODUCT__`) staan met merk en artikelnummer.
- **Zoeken in alles wat je kocht.** Het zoekveld bovenaan Bestellingen zoekt in de producten van je Jumbo-bestellingen (naam en merk) en toont per product wanneer en hoe vaak je het kocht.
- **Privacy.** Het afleveradres, het winkeladres, je klantnummer en het reserveringsnummer worden niet bewaard en komen niet in het verkennerrapport.
- **MCP.** `list_jumbo_orders` en `get_jumbo_order` (alleen lezen, uit de lokale kopie).
- **Verkenner.** **Jumbo-routes verkennen** toont nu ook de bestellingenlijst (aantallen, statussen, vorm van één bestelling, via welk transport) en de bestelling met producten (aantal regels, vorm, en welke variant van de aanvraag werkt).
- **Bestellingen per winkel.** `orders.js` werkt nu per winkel (de keuze bovenaan Winkels): bij Jumbo ontbreken het bestelmandje en de kassabonnen, bij AH verandert er niets (het bestelmandje en de bestellingen van AH blijven "binnenkort").

### Goed om te weten

- **Producten van een bestelling: nog niet bevestigd.** De opgeslagen aanvraag van de lijst is bekend, die van één bestelling nog niet. MealieShopper probeert daarom gewone aanvragen (`order(orderId: …)` en varianten) met de veldnamen uit het antwoord van de website. Weigert Jumbo die, dan staat de lijst er wel en meldt de synchronisatie de reden; de verkenner laat zien wat er gebeurt. Het herstel is dan een nieuwe constante in `mealieshopper/jumbo.py` met de `operationName`, hash en variabelen van de aanvraag uit het tabblad Netwerk.
- **Niet gedaan:** winkelbonnen (je account heeft er geen; de synchronisatie meldt wel hoeveel Jumbo er kent) en lopende bestellingen. Alles is getest met nagebootste antwoorden in de vorm van de echte; probeer het synchroniseren op je eigen server en draai de verkenner.

## v1.13.1

*English summary: Fixes the Updates tab showing raw text keys such as "update.title". The browser kept using the old cached text files because the asset version was not raised in 1.13.0; it is now. After updating, a hard refresh (Ctrl+Shift+R) may still be needed once.*

### Opgelost in 1.13.1

- **Teksten bij Beheer > Updates.** In 1.13.0 stonden er sleutelnamen zoals `update.title` in plaats van de teksten, omdat de browser nog de oude, bewaarde tekstbestanden gebruikte. Het versienummer van de scripts en teksten (`ASSET_VERSION`) is verhoogd, zodat de nieuwe bestanden worden opgehaald. Zie bij een hardnekkige cache één keer een harde refresh (`Ctrl`+`Shift`+`R`) of open de app opnieuw.

## v1.13.0

*English summary: Settings has a new Updates tab with an update check. MealieShopper looks at GitHub for the latest release and compares it with the running version. When a newer version exists, the administrator gets a notice under the tab bar (with "View" and "Later"), a dot on the Settings tab and on Updates, and the Updates tab links to the release notes and says how to update. The answer is kept for six hours (or ask again with "Check now"), nothing about you or the installation is sent, and `MEALIESHOPPER_UPDATE_CHECK=false` turns it off.*

### Nieuw in 1.13.0

- **Updatecheck.** Bij **Beheer > Updates** staat welke versie er draait en wat de nieuwste release op GitHub is, met een knop **Nu controleren**, een link naar de release-notes en uitleg hoe je bijwerkt (nieuwste image ophalen en de container herstarten).
- **Melding bij een nieuwe versie.** Is er een nieuwere release, dan krijgt de beheerder onder de tabbalk een melding met **Bekijken** (brengt je naar Beheer > Updates) en **Later** (verbergt de melding voor die versie; bij de volgende versie komt hij terug). Het bolletje bij **Beheer** en **Updates** blijft staan tot je bijwerkt. Gewone gebruikers zien niets.
- **Rustig voor GitHub en voor je privacy.** De app vraagt alleen de laatste gepubliceerde release op, hooguit om de zes uur en met een rem op **Nu controleren**. Er gaat geen informatie over jou of de installatie mee. Is GitHub niet bereikbaar, dan staat er een melding en blijft een eerder gevonden update zichtbaar.

### Goed om te weten

- Uitzetten kan met de omgevingsvariabele `MEALIESHOPPER_UPDATE_CHECK=false` (zie de README); het tabblad zegt dan dat de check uit staat.
- Alleen de beheerder ziet en gebruikt de updatecheck (`/api/update`).

## v1.12.0

*English summary: There is a new Basket tab with a store list (Albert Heijn, Picnic, Jumbo, as far as linked). Picnic's basket moved here from the planner, with the quantity, remove and empty controls and the delivery slots; Jumbo's basket is shown read-only; the Albert Heijn order basket moved here from Stores > Orders (it still says "coming soon"). After filling a Picnic or Jumbo basket from the week menu, a "View basket" button takes you to the tab. The tab bar now has seven tabs, so two short Dutch labels got shorter ("Menu", "Import") to fit a phone.*

### Nieuw in 1.12.0

- **Tabblad Mandje.** Na het Weekmenu staat nu een eigen tabblad met een keuzelijst voor de winkels die gekoppeld zijn: **Albert Heijn**, **Picnic** en **Jumbo**. De keuze wordt onthouden in je browser. Met het vernieuw-icoon laad je het mandje opnieuw.
- **Picnic:** het mandje dat je eerder in de planner opende staat hier, met aantallen aanpassen, product weghalen, mandje legen (na een bevestiging), wat je moet betalen en wat je lidmaatschap bespaart. De eerstvolgende bezorgmomenten staan eronder en blijven staan als je het mandje aanpast.
- **Jumbo:** je echte Jumbo-mandje, alleen lezen (producten, aantallen, prijzen en totaal). Aanpassen en bestellen doe je bij Jumbo.
- **Albert Heijn:** het bestelmandje is van **Winkels > Bestellingen** naar hier verhuisd. AH levert het nog niet, dus er staat nog dat het binnenkort komt; zodra dat kan, werkt het synchroniseren vanuit dit tabblad.
- **Vanuit de planner:** na het vullen van een Picnic- of Jumbo-mandje staat er een knop **Mandje bekijken** die je naar het tabblad brengt, met die winkel gekozen. Het mandje en de bezorgmomenten staan niet meer onder het weekmenu.

### Goed om te weten

- De tabbalk heeft nu **zeven tabs**. Zodat ze op een telefoon van 360 px naast elkaar passen, zijn twee korte labels in de onderbalk korter geworden: *Weekmenu* is *Menu* en *Importeren* is *Import*. De lange namen in de bovenbalk op een groot scherm blijven hetzelfde.
- Het Jumbo-mandje is bewust alleen-lezen: het aanpassen van aantallen bij Jumbo is nog niet uitgeprobeerd.

## v1.11.0

*English summary: Your own Jumbo lists ("Mijn lijstjes") and Favourites are now in MealieShopper next to the shopping list, with the same cards, sync, search, adjustable quantities and "Add to basket" as the AH lists. Jumbo serves them through stored (persisted) queries that the website itself uses, so MealieShopper sends the same requests; if Jumbo renews those hashes with a new site version the explorer reports PersistedQueryNotFound and the two hashes in jumbo.py need refreshing. The old "Jumbo-lijstje" is now called "Boodschappenlijstje", and the planner choice "Jumbo (lijstje)" became "Jumbo (boodschappenlijstje)". Two read-only MCP changes: list_jumbo_lists, and get_jumbo_list takes an optional list name. Built from the real answers of jumbo.com but tried only with mocked responses so far.*

### Nieuw in 1.11.0

- **Je eigen Jumbo-lijstjes en Favorieten.** Onder **Winkels > Lijstjes** (keuze *Jumbo*) staan nu alle lijstjes uit *Mijn lijstjes* van Jumbo, naast het boodschappenlijstje. Elk lijstje werkt als een AH-lijstje: synchroniseren naar een lokale kopie, zoeken, het aantal aanpassen (jouw aanpassing blijft bij een volgende synchronisatie staan en je kunt terugzetten naar Jumbo's aantal) en met `In mandje` alles in je Jumbo-mandje zetten. De producten hebben merk, pakmaat, prijs, aanbieding en een link naar het product. Lijstjes met meer dan 25 producten haalt MealieShopper in meerdere stappen op.
- **Opgeslagen queries.** Jumbo's website vraagt deze lijstjes met *persisted queries*: alleen een hash en de variabelen, geen querytekst. MealieShopper stuurt dezelfde aanvraag (`GetCustomerProductLists` en `GetProductList`). Welk transport (POST of GET) en welke clientnaam Jumbo wil is niet bekend; MealieShopper probeert ze na elkaar en onthoudt wat werkt. Verandert Jumbo de hashes, dan geeft de app een foutmelding en toont **Jumbo-routes verkennen** `PersistedQueryNotFound`; zie `PERSISTED_QUERIES` in `mealieshopper/jumbo.py` en de README om ze te verversen.
- **Het boodschappenlijstje** heet nu *Boodschappenlijstje* (voorheen *Jumbo-lijstje*), en de keuze in de planner *Jumbo (boodschappenlijstje)*, zodat het niet met je eigen lijstjes verward wordt. Het blijft het lijstje met losse regels waar de planner ingrediënten op zet.
- **MCP, alleen lezen:** nieuw `list_jumbo_lists` (boodschappenlijstje, eigen lijstjes en Favorieten); `get_jumbo_list` neemt een optioneel `list` (id of deel van de naam, bijvoorbeeld "favorieten"), zonder opgave het boodschappenlijstje.
- **Jumbo-routes verkennen** probeert nu ook beide opgeslagen queries: hoeveel eigen lijstjes en favorieten, de vorm van een lijstje en van een product, en met welk transport en welke clientnaam het werkte. Zonder titels, prijzen of id's.

### Goed om te weten

- De antwoordvormen komen uit de echte aanvragen van jumbo.com (dank aan wie ze uit de browser kopieerde); de code is tot nu toe alleen met nagebootste antwoorden in die vorm getest. Draai **Jumbo-routes verkennen** en synchroniseer één lijstje; meld wat afwijkt.
- Je volgt andere lijstjes op Jumbo? Die toont MealieShopper niet (Jumbo geeft er alleen een aantal van).
- Nog niet voor Jumbo: bonnen, bestellingen en de stap in de installatiehulp.

## v1.10.0

*English summary: You can now manage your Picnic basket from MealieShopper: change the quantity of a product, remove a product, or empty the whole basket (with a confirmation). Changes go straight to your real Picnic basket; an order is never placed. These are not available through MCP. The basket editing routes follow the unofficial Picnic API and have not been tried against a live basket yet.*

### Nieuw in 1.10.0

- **Je Picnic-mandje beheren in de planner.** Kies je Picnic als supermarkt, dan staat naast de keuzelijst de knop **Picnic-mandje bekijken**. Het mandje verschijnt ook vanzelf na het vullen. Per product pas je het aantal aan (min, plus of typen), haal je het uit het mandje (kruisje) of leeg je het hele mandje, dat laatste na een bevestiging. Onderaan staat wat je moet betalen en wat je lidmaatschap bespaart.
- **Zo kun je het weekmenu vaker naar Picnic sturen** zonder dat aantallen verdubbelen: kijk na het vullen in het mandje en zet aantallen terug waar nodig.
- **Directe wijzigingen, nooit een bestelling.** Elke aanpassing gaat meteen naar je echte Picnic-mandje; bestellen en een bezorgmoment kiezen doe je in de Picnic-app.
- Nieuwe routes: `PUT /api/picnic/cart/items/<product>` met `{"quantity": n}` (0 haalt het product weg, 0 tot en met 99) en `POST /api/picnic/cart/clear` met `{"confirm": true}`. Zonder die bevestiging gebeurt er niets.
- **Niet via MCP.** Net als het vullen kan een AI-assistent het mandje niet aanpassen of legen; de MCP-tools blijven alleen-lezen.

### Goed om te weten

- Picnic kent alleen toevoegen en verwijderen. Een exact aantal zetten leest daarom eerst het mandje en voegt het verschil toe of haalt het weg.
- **Nog niet met een echt gevuld mandje geprobeerd.** De routes (`cart/add_product`, `cart/remove_product`, `cart/clear`) volgen de community-libraries; het mandje van het testaccount was leeg. Weigert Picnic `cart/clear`, dan haalt MealieShopper elk product apart weg. Meld wat afwijkt.

### Intern

- Nieuwe test `test_static_scripts`: faalt als twee scripts in `static/` dezelfde globale functie verschillend definiëren (de oorzaak van het Jumbo-probleem uit 1.9.1).

## v1.9.1

*English summary: Fixes Picnic (and Jumbo) error messages that had become too vague: a refused login, a wrong code or an expired link explain themselves again. Messages that can contain a store's own answer still never reach the screen.*

### Opgelost in 1.9.1

- **Duidelijke meldingen bij Picnic en Jumbo.** Een geweigerde login, een onjuiste of verlopen code en een verlopen koppeling zeiden alleen nog "aanvraag ongeldig". Ze noemen nu weer wat er aan de hand is, bijvoorbeeld "controleer e-mailadres en wachtwoord".
- **Het antwoord van de winkel zelf blijft uit beeld.** Die meldingen zijn teksten van MealieShopper. Wat Picnic of Jumbo zelf terugstuurt, gaat alleen naar het logboek; bij een storing zie je een algemene melding (`Picnic fout`).
- **Jumbo stond niet meer in de supermarktkeuze van de planner.** Bij het samenvoegen van de Picnic- en Jumbo-wijzigingen was in `picnic.js` een oude `renderStoreChoice` teruggekomen die de nieuwe uit `app.js` overschreef (alleen AH en Picnic, en een gekozen Jumbo sprong terug naar AH). Die kopie is weg. Een nieuwe test (`test_static_scripts`) faalt voortaan als twee scripts dezelfde functie verschillend definiëren.
- **Koppeling controleren bij Picnic** toont bij een verlopen koppeling de uitleg (opnieuw koppelen) en bij andere fouten alleen het soort fout.

## v1.9.0

*English summary: The Jumbo shopping list is now in MealieShopper. The tab "AH Items" is renamed "Stores" and, once Jumbo is linked, has a store choice at the top: the Jumbo list shows up under Lists with the same cards, quantities and "Add to basket" as the AH lists. The planner gets a fourth choice, "Jumbo (list)", which puts the ingredients on the Jumbo list instead of in the basket. A new read-only MCP tool, get_jumbo_list, shows the list. Also fixes a stale script that hid Jumbo from the planner's store list. Not yet tried against a live Jumbo list: the explorer under Settings > Jumbo now checks the list routes too.*

### Nieuw in 1.9.0

- **Het Jumbo-lijstje.** Onder **Winkels > Lijstjes** zie je, na de keuze *Jumbo*, het ene lijstje van Jumbo met dezelfde kaart, synchroniseer-knop, zoekveld en aanpasbare aantallen als de AH-lijstjes. Het lijstje bestaat uit tekst en soms een product; voor regels met een product haalt MealieShopper titel, pakmaat en prijs erbij. Jumbo kent geen aantallen op het lijstje, dus die zijn lokaal (standaard 1). Afgevinkte regels slaat MealieShopper over.
- **`In mandje`** zet het hele lijstje in het Jumbo-mandje: regels met een product gaan direct, regels met alleen tekst zoekt MealieShopper eerst op in het assortiment van Jumbo. Wat niet te vinden of toe te voegen is, meldt de app.
- **Ingrediënten op het Jumbo-lijstje.** In de planner staat nu naast *Jumbo* ook *Jumbo (lijstje)*: de ingrediënten gaan dan op het boodschappenlijstje in plaats van in het mandje. Wat er al op staat slaat MealieShopper over; staat het lijstje in de Jumbo-app uit, dan zet MealieShopper het aan.
- **Het tabblad heet nu Winkels** (voorheen *AH Items*). Is Jumbo gekoppeld, dan staat bovenaan een keuzelijst met Albert Heijn en Jumbo; bij Jumbo ontbreken de recepten. De teksten in de lijsten zijn winkel-neutraal gemaakt ("In mandje", "Op lijstje: 2" in plaats van "AH: 2").
- **Nieuwe MCP-tool, alleen lezen:** `get_jumbo_list`. Een regel die geen product is, staat erin met alleen zijn tekst. Het lijstje vullen kan niet via MCP.
- **Jumbo-routes verkennen** controleert nu ook het lijstje: de vorm van `shoppingList`, het schrijfpad `editShoppingListItems` (alleen met een lege lijst, dus er wordt niets toegevoegd) en de productgegevens van een regel. Bij een schemafout van Jumbo (een veld of type dat niet bestaat) toont het rapport nu de validatiemelding van Jumbo zelf (alleen veld- en typenamen uit onze eigen query, geen persoonsgegevens); andere fouten blijven alleen hun type tonen.
- Nieuwe routes onder `/api/jumbo/lists` en `/api/jumbo/list`.

### Opgelost in 1.9.0

- **Jumbo ontbrak in de keuzelijst van de planner.** Een verouderd tweede `renderStoreChoice` in `static/picnic.js` (teruggekomen bij het samenvoegen van de Picnic-bezorgmomenten) overschreef de versie die AH, Picnic en Jumbo kent. Het duplicaat is weg en een test bewaakt dat een scriptfunctie maar één keer bestaat.

### Goed om te weten

- De vorm van het Jumbo-lijstje komt uit het community-project `jumbo-mcp`. Of Jumbo precies deze velden en typenamen kent is nog **niet met een echt lijstje bevestigd**; elke query valt terug op een variant zonder foutfragment (zoals bij het mandje bleek nodig). Draai **Jumbo-routes verkennen**, zet één regel op het lijstje en meld wat afwijkt. Het schrijven naar het lijstje is een echte wijziging in je Jumbo-account; er wordt niets besteld.
- Nog niet voor Jumbo: bonnen, bestellingen en de stap in de installatiehulp (volgen in aparte versies).

## v1.8.0

*English summary: Picnic now shows the next available delivery slots under the filled basket and through a new read-only MCP tool, `list_picnic_delivery_slots`. The basket also reports what you actually pay and what your membership saves. The Picnic route explorer checks more routes (account, lists, store, suggestions, product page) so the next steps can be built on real answers. Choosing a slot and placing an order remain in the Picnic app.*

### Nieuw in 1.8.0

- **Bezorgmomenten van Picnic.** Na het vullen van je Picnic-mandje laat de planner de eerstvolgende beschikbare bezorgmomenten zien, met de minimale bestelwaarde. Picnic levert die in het mandje-antwoord mee; ze zijn dus live en er wordt niets gekozen of gereserveerd. Het bezorgmoment kies je zelf in de Picnic-app. Nieuwe route: `GET /api/picnic/slots`.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_delivery_slots` (beschikbare bezorgmomenten met begin, einde, uiterste bestelmoment en minimale bestelwaarde). Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **Mandje met echt totaal.** `get_picnic_basket` en `GET /api/picnic/cart` melden nu ook wat je werkelijk betaalt (`checkoutTotal`, met bezorgkosten en statiegeld) en wat je lidmaatschap bespaart (`savings`).
- **Picnic-routes verkennen kijkt verder.** Naast zoeken, mandje en leveringen worden nu ook account, lijstjes, winkel, suggesties en de productpagina aangeroepen. Alleen lezen en zonder persoonlijke gegevens in het rapport. Zo zien we welke uitbreidingen (lijstjes, productinfo) mogelijk zijn.

### Goed om te weten

- Het zoeken, het mandje en de leveringsroutes zijn bevestigd met een echt Picnic-account: de verkenner gaf voor alle vijf routes een antwoord. De vorm van de bezorgmomenten is daaruit afgeleid. De vorm van een levering en van een gevuld mandje is nog niet gezien (het account had geen leveringen en een leeg mandje); meld wat afwijkt.
- Een bezorgmoment kiezen of een bestelling plaatsen kan niet vanuit MealieShopper, en dat blijft zo.

## v1.7.1

*English summary: Fixes adding products to the Jumbo basket. The explorer, run on a real server, showed that Jumbo's addBasketLines answer cannot be a BasketError, so every add would have failed validation. The query no longer names that type and an error now reports the type Jumbo answered with. The explorer also shows the type name and tries to list the possible answers. The same run confirmed that Jumbo accepts the server, the token refresh, search and reading the basket work.*

### Opgelost in 1.7.1

- **Producten toevoegen aan het Jumbo-mandje** mislukte altijd met een validatiefout van Jumbo (`Fragment cannot be spread here as objects of type "AddBasketLinesResult" can never be of type "BasketError"`). De foutvariant van `addBasketLines` heet in Jumbo's schema anders dan bij `activeBasket`. De query noemt dat type nu niet meer; bij een weigering meldt de app de typenaam die Jumbo teruggaf (bijvoorbeeld `Jumbo weigerde het toevoegen aan het mandje (…)`).
- **Jumbo-routes verkennen** toont bij het (lege) toevoegen nu de typenaam van het antwoord (`Basket` of Jumbo's foutvariant) en probeert de mogelijke antwoorden op te vragen; introspectie staat bij Jumbo meestal uit, en dan zegt de foutmelding dat.

### Goed om te weten

- Het rapport van de verkenner op een echte server **bevestigde**: Jumbo laat de server toe (`auth.jumbo.com` en `www.jumbo.com/api/graphql` antwoorden gewoon), het token vernieuwen werkt, zoeken werkt (met pakmaat en plaatje) en het mandje lezen werkt.
- Het toevoegen zelf is met deze versie nog **niet met een echt product bevestigd**. Zet één product in het Jumbo-mandje en meld de uitkomst; de foutmelding noemt nu de typenaam, waarmee een volgende versie ook de reden kan tonen.

## v1.7.0

*English summary: MealieShopper can now fill a Jumbo basket too. Jumbo moved from REST to Auth0 and GraphQL since the removed v1.2.0 link, so linking now works like Albert Heijn: the administrator logs in in their own browser and pastes the jumbo://callbacks/login line from the console. No headless browser, no stored password. The planner's supermarket list now offers Albert Heijn, Picnic and Jumbo. Jumbo may refuse a server address, so the explorer under Settings > Jumbo first checks reachability. Not yet tried against a live Jumbo account.*

### Nieuw in 1.7.0

- **Jumbo naast Albert Heijn en Picnic.** De beheerder koppelt Jumbo onder **Beheer > Jumbo**: inloggen in je eigen browser en de regel `jumbo://callbacks/login?code=...` uit de console plakken, net als bij AH. Jouw browser lost de bot-controle van Jumbo op, dus er is **geen headless browser (Chromium/Selenium)** nodig. MealieShopper bewaart geen wachtwoord, alleen het refresh-token van Jumbo, versleuteld, en bewaart steeds de nieuwste als Jumbo het vervangt.
- **Zoeken en mandje.** Per ingrediënt zoekt MealieShopper de beste Jumbo-producten (met aanbieding, pakmaat en een link naar het product), rekent het aantal verpakkingen uit en laat twijfelgevallen eerst controleren. De producten komen direct in je echte Jumbo-mandje; er wordt nooit een bestelling geplaatst. Mislukt één product, dan staan de andere er wel in en meldt de app welke ontbreken.
- **De keuzelijst in de planner** biedt nu Albert Heijn, Picnic en Jumbo, voor zover gekoppeld. De controlelijst toont bij AH en Jumbo een link naar het product (`Bekijk product`).
- **Twee nieuwe MCP-tools, alleen lezen:** `search_jumbo_products` en `get_jumbo_basket`. Het mandje vullen kan niet via MCP.
- **Jumbo-routes verkennen** (alleen de beheerder): controleert eerst of Jumbo jouw server toelaat (`auth.jumbo.com` en `www.jumbo.com/api/graphql`) en roept daarna zoeken en het mandje aan; het mandje vullen alleen met een lege lijst. Zonder namen, prijzen of id's, dus veilig te delen.
- Nieuwe routes onder `/api/jumbo/` (koppelen, zoeken, mandje); koppelen en verkennen zijn alleen voor de beheerder.

### Goed om te weten

- **Jumbo kan een server weigeren.** Een eerdere Jumbo-koppeling (v1.2.0) liep vast op de bot-bescherming van Jumbo voor server- en datacenter-adressen. Die routes zijn inmiddels anders (Auth0 en GraphQL in plaats van REST), maar of Jumbo jouw server toelaat is niet vooraf te weten: draai **Jumbo-routes verkennen**. Weigert Jumbo, dan werkt de koppeling vanaf die plek niet en komt er geen headless browser terug.
- De Jumbo-API is **niet officieel** en zonder bericht te wijzigen. De aanroepen volgen de community-projecten `jumbo-mcp` en `jumbo-cli`. Deze versie is getest met nagebootste antwoorden en **nog niet met een echt Jumbo-account**. Vooral het mandje vullen (`addBasketLines`) is alleen elders met cookie-login gezien; gebruik de verkenner en meld wat afwijkt.
- Nog niet voor Jumbo: boodschappenlijstje, kassabonnen, bestellingen, de installatiehulp en recepten.

### Intern

- De winkelmand-route die producten zoekt en in het mandje zet is gedeeld tussen Picnic en Jumbo (`store_cart` in `app.py`); `static/app.js` kent de winkels in één tabel (`STORES`). De tekst bij de productlink in de controlelijst is winkel-neutraal gemaakt.

## v1.6.0

*English summary: MealieShopper can now fill a Picnic basket as well as an Albert Heijn one. The administrator links Picnic under Settings > Picnic (email, password and the code Picnic sends), and the planner then lets you choose the supermarket. Products go straight into the real Picnic basket; an order is never placed. Three read-only MCP tools show Picnic products, your basket and your deliveries. The Picnic API is unofficial and has not been tried against a live account yet: use the explorer under Settings > Picnic to check which routes work.*

### Nieuw in 1.6.0

- **Picnic naast Albert Heijn.** De beheerder koppelt Picnic onder **Beheer > Picnic** met e-mailadres, wachtwoord en de code die Picnic per sms of e-mail stuurt. Daarna kies je in de planner naar welke supermarkt het weekmenu gaat; de keuze wordt onthouden in je browser. Het wachtwoord wordt niet bewaard, alleen de sessie die Picnic teruggeeft, versleuteld. Picnic vernieuwt die sessie zelf; MealieShopper bewaart de nieuwste.
- **Zelfde controlelijst als bij AH.** Per ingrediënt zoekt MealieShopper de beste Picnic-producten, rekent het aantal verpakkingen uit en laat je twijfelgevallen eerst controleren. Een Picnic-product heeft geen webpagina, dus de regel `Bekijk bij AH` ontbreekt daar.
- **Direct in het echte Picnic-mandje.** Anders dan bij AH (een boodschappenlijstje) komen de producten in je winkelmandje. Er wordt nooit een bestelling geplaatst: controleer het mandje in de Picnic-app en reken daar af. Mislukt één product, dan staan de andere er wel in en meldt de app welke ontbreken.
- **Drie nieuwe MCP-tools, alleen lezen:** `search_picnic_products`, `get_picnic_basket` en `list_picnic_deliveries`. Ze halen live bij Picnic op (zonder adres) en zijn bij AI-apps afzonderlijk uit te zetten. Het mandje vullen kan niet via MCP.
- **Picnic-routes verkennen** (alleen de beheerder): toont welke onofficiële Picnic-routes werken en welke velden erin zitten, zonder namen, adressen, prijzen of id's.
- Nieuwe routes onder `/api/picnic/` (koppelen, zoeken, mandje, leveringen); koppelen en verkennen zijn alleen voor de beheerder. Optioneel: `PICNIC_COUNTRY_CODE` (`NL`, `DE` of `FR`).

### Goed om te weten

- De Picnic-API is **niet officieel** en zonder bericht te wijzigen; de aanroepen volgen de community-libraries `python-picnic-api2` en `picnic-api`. Deze versie is getest met nagebootste antwoorden en **nog niet met een echt Picnic-account**: gebruik **Picnic-routes verkennen** om te zien wat er werkt, en meld wat afwijkt.
- Nog niet voor Picnic: lokale kopie van leveringen, de installatiehulp en lijstjes, kassabonnen en recepten (Picnic kent die niet op dezelfde manier).

### Intern

- De verpakkingsrekenmachine (`parse_pack_size`, `assess_packs`, `normalize_quantity`) staat nu in `mealieshopper/packs.py` en is voor alle supermarkten gedeeld; `ah.py` blijft ze doorgeven, dus bestaande aanroepen werken ongewijzigd. De controlelijst-route is gedeeld tussen AH en Picnic.

## v1.5.3

*English summary: Click the logo in the header to go to the start screen (the first tab).*

### Nieuw in 1.5.3

- **Het logo in de kop is klikbaar** en brengt je naar het startscherm (Weekmenu naar winkel), vanaf elke tab. Met Ctrl, Cmd of de middelste muisknop opent het startscherm in een nieuw tabblad. Het logo is ook met het toetsenbord te bereiken.

## v1.5.2

*English summary: You can now search within your receipts: type a product (for example "soda") and see which receipts contain it, with the matching lines, and the matching lines are highlighted when you open a receipt.*

### Nieuw in 1.5.2

- **Zoeken in kassabonnen.** Typ een product (bijvoorbeeld "frisdrank") in het zoekveld en je ziet op welke kassabonnen het staat, met per bon de regels die passen. Zoeken negeert hoofdletters en accenten en alle woorden moeten erin staan. Open je een bon vanuit de zoekresultaten, dan zijn de passende regels geel gemarkeerd.
- Bonnen waarvan de producten nog niet zijn opgehaald kunnen nog niet worden doorzocht; de app zegt hoeveel dat er zijn en vraagt om opnieuw te synchroniseren.
- Nieuwe route `GET /api/ah/receipts/search?q=`, lokaal en alleen voor ingelogde gebruikers.

## v1.5.1

*English summary: Receipts now look like an Albert Heijn paper receipt. Delivered orders are fetched from AH (the latest 10 closed orders per request) and an empty order basket is no longer an error. Because AH's order routes are not available right now, the order basket and orders show a "coming soon" line and only receipts are synced. The explorer was extended to find out how to reach further back than 10 delivered orders.*

### Nieuw in 1.5.1

- **Kassabonnen zien eruit als een AH-kassabon**: een strook papier met aantal, omschrijving, prijs en bedrag, de bonusregels, het totaal en de betaalwijze, zonder eurotekens zoals op papier. Het past ook op een telefoon en blijft licht in de donkere modus.
- **Het bestelmandje en de bestellingen komen binnenkort.** AH had een storing en de bestelroutes zijn nog niet beschikbaar, dus de app toont daar een regel dat het binnenkort komt en het synchroniseer-icoon haalt alleen kassabonnen op. De code staat klaar: in `static/orders.js` zet je `ORDERS_COMING_SOON` op `false` om mandje en bestellingen weer te tonen. De bijbehorende MCP-tools blijven bestaan en zijn bij AI-apps uit te zetten.
- **Afgeronde bestellingen** haalt MealieShopper nu ook op uit AH (de verkenner liet zien dat dat kan met status `CLOSED`). AH geeft er per keer de laatste 10; wat is afgeleverd wordt voor altijd bewaard, geannuleerde bestellingen worden overgeslagen. Van maximaal 15 bestellingen per keer komen de producten binnen; de rest volgt bij de volgende synchronisatie.
- **Een leeg bestelmandje is geen fout meer.** Zonder actieve bestelling antwoordt AH met een 404; dat geldt nu als een leeg mandje.
- **Verkenner uitgebreid** (alleen de beheerder): een lijst in het rapport toont de samenvoeging van alle elementen (alle statussen, getaltypes) en de verkenner onderzoekt nu hoe je verder terug komt dan 10 afgeronde bestellingen (argumenten van AH, vijf pagineringsvarianten) en wat er in een afgeleverde bestelling zit.

## v1.5.0

*English summary: MealieShopper can now keep your Albert Heijn order basket, orders and in-store receipts as a local copy, so you can browse and search your purchase history without a connection to AH, and your AI assistant can query it. Delivered orders are archived permanently once an open order has been delivered. The administrator can switch each MCP tool off individually. An owner-only explorer reports which of the unofficial AH routes work, to find out how to fetch past orders.*

### Nieuw in 1.5.0

- **Bestelmandje, bestellingen en kassabonnen** onder AH Items > Bestellingen. Het bestelmandje is de actieve bestelling bij AH (en dus iets anders dan de boodschappenlijst waar "In AH-mandje" naartoe schrijft; dat staat ook in de interface). Open bestellingen komen uit AH; kassabonnen zijn je aankopen in de winkel.
- **Alles staat in een lokale kopie.** Bekijken en zoeken gaan uit die kopie en vragen nooit iets aan AH, dus de bestelhistorie is ook te zien als AH niet bereikbaar is. Alleen het synchroniseer-icoon praat met AH; mislukt dat, dan blijft de kopie staan en zie je de fout.
- **Slim synchroniseren.** Een volgende synchronisatie haalt alleen op wat nieuw is. Het bestelmandje is een momentopname. Een open bestelling wordt bijgewerkt zolang hij open is; verdwijnt hij uit de open bestellingen, dan haalt MealieShopper hem een laatste keer op en bewaart hem als **afgeleverd**, voor altijd. Kassabonnen komen er in porties van 40 bij, zodat een synchronisatie kort blijft.
- **Zoeken in al je aankopen**: wanneer en hoe vaak kocht ik iets, in bestellingen en op kassabonnen, met het detail van elke bestelling en bon.
- **AI-assistenten** kunnen dit bevragen met zes nieuwe tools (alleen lezen, uit de lokale kopie, met het tijdstip van de laatste synchronisatie): `get_ah_basket`, `list_ah_orders`, `get_ah_order`, `list_ah_receipts`, `get_ah_receipt` en `search_ah_purchases`. Het toestemmingsscherm noemt dit.
- **Tools afzonderlijk uitzetten.** De beheerder zet bij AI-apps elke MCP-tool los aan of uit, voor alle gebruikers. Een uitgezette tool verdwijnt uit het overzicht van de assistent en wordt geweigerd als hij toch wordt aangeroepen; andere gebruikers zien in de lijst dat hij is uitgezet.
- **Privacy:** het afleveradres wordt niet opgehaald en niet bewaard, alleen datum en tijdvak. Bestellingen en kassabonnen horen bij het gedeelde AH-account en zijn voor alle gebruikers te zien, net als de lijstjes.
- **AH-routes verkennen** (alleen de beheerder, onder Beheer > Albert Heijn): roept de onofficiële leesroutes aan en meldt welke werken en welke velden erin zitten, ook welke statussen AH voor bestellingen kent. Er wordt niets gewijzigd of bewaard en het rapport bevat geen namen, adressen, prijzen of id's. Daarmee zoeken we uit hoe afgeleverde bestellingen uit het verleden zijn op te vragen.
- Een bestelling waarvan je het nummer kent, is al op te halen met de code (`add_order_by_id`); de knop daarvoor volgt zodra de verkenner laat zien hoe AH de lijst van afgeleverde bestellingen geeft.

### Goed om te weten

- Deze AH-routes zijn onofficieel en niet door ons tegen een echt account geprobeerd: ze volgen de community-bibliotheek `appie-go`. Gebruik **AH-routes verkennen** als iets niet werkt en deel het rapport.

## v1.4.1

### Fixes

- **Aangepaste aantallen in een AH-lijstje blijven staan bij synchroniseren.** Synchroniseren voegt nieuwe items toe en haalt weg wat bij AH is verdwenen, maar laat een aantal dat jij hebt aangepast met rust; alleen het aantal van AH (waar de resetknop naar terugzet) wordt ververst. Regels worden nu gekoppeld op het id dat AH aan de regel geeft (en daarna op het product), zodat een regel dezelfde regel blijft bij een andere volgorde, een nieuw id of een product dat er twee keer in staat. Het bijwerken gebeurt in één transactie, zodat een aanpassing tijdens het synchroniseren niet verloren gaat. Na het synchroniseren staat er wat er is gebeurd, bijvoorbeeld "2 nieuwe items, 1 aangepast aantal behouden."
- **Een mislukte opslag van een aantal is nu zichtbaar.** Mislukte het opslaan, dan werden de aantallen stil teruggezet en wiste het opnieuw laden de foutmelding; nu verschijnt de melding na het laden.
- **De resetknop vraagt eerst om bevestiging.** Je ziet hoeveel aangepaste aantallen worden teruggezet naar die van AH, en kunt annuleren.
- **Een AI-app koppelen terwijl je nog niet bent ingelogd werkt nu.** De toestemmingspagina stuurde je naar de inlog, maar na het inloggen bleef je in de app staan en kwam je niet meer bij het toestemmingsscherm. Nu keert de pagina na het inloggen terug naar de toestemming, en alleen naar een adres van `/oauth/authorize` (geen open redirect). Dit gebeurde alleen als je op dat moment niet was ingelogd; wie al was ingelogd merkte er niets van.

## v1.4.0

*English summary: MealieShopper now has an MCP server, so you can ask Claude, ChatGPT or GitHub Copilot about your week menu, recipes and AH lists and let them import a recipe into Mealie. Login is OAuth 2.1 with PKCE; every token belongs to the user who gave consent. There is deliberately no tool that fills the AH basket or deletes anything.*

### Nieuw in 1.4.0

- **MCP-server** op `<adres>/mcp` (Streamable HTTP, zonder sessies of streams), zodat je Claude, ChatGPT of Copilot je weekmenu, recepten en lijstjes kunt laten bekijken en een recept in Mealie kunt laten importeren. Tools: `get_week_menu`, `search_allerhande_recipes`, `list_saved_ah_recipes`, `list_ah_lists`, `get_ah_list`, `search_ah_list_items`, `search_ah_products` en `import_recipe_to_mealie`. Er is bewust **geen** tool die het AH-mandje vult of iets verwijdert; tools zijn gemarkeerd als alleen-lezen of (de import) als aanmakend.
- **Inloggen met OAuth 2.1** voor alle assistenten: metadata volgens RFC 9728 en RFC 8414, dynamic client registration (alleen publieke clients, alleen https-, loopback- of app-redirects), verplichte PKCE (S256), een toestemmingsscherm in het Nederlands en Engels, en intrekken via `/oauth/revoke`.
- **Per gebruiker.** Elke code en elk token hoort bij de gebruiker die toestemming gaf en geeft alleen die rechten. Een nieuw wachtwoord, uitgezette tweestapsverificatie, een nieuwe inloglink of het verwijderen van de gebruiker maakt zijn of haar tokens ongeldig.
- **Veilig bewaard.** Tokens staan alleen als hash in de database. Toegangstokens gelden een uur; verversingstokens 30 dagen en worden bij elk gebruik vervangen. Een verversingstoken of code die twee keer wordt gebruikt, trekt de hele reeks in.
- **Beheer > AI-apps** voor elke gebruiker: het adres van de MCP-server met een kopieerknop, uitleg voor Claude, Claude Code, ChatGPT en Copilot, wat de assistent kan, en de gekoppelde apps met een knop om toegang in te trekken. Bij een adres zonder https volgt een waarschuwing.
- **Instellen in de app, alleen door de beheerder.** Bij Beheer > AI-apps staat een schakelaar om de MCP-server aan of uit te zetten en het publieke adres van MealieShopper. Dat adres wordt automatisch herkend (met de `X-Forwarded-*`-headers van je proxy) en kan worden overschreven; voorrang: ingesteld in de app, dan `MEALIESHOPPER_PUBLIC_BASE_URL`, dan automatisch. Het adres gebruiken ook de uitnodigingslinks, de AI-koppeling en het voorbeeldplaatje. De schakelaar gaat voor op `MEALIESHOPPER_MCP`, dat nu alleen de beginwaarde geeft. Gewone gebruikers zien de instellingen niet en krijgen een 403 op `/api/server-settings`; verandert het adres, dan moeten gekoppelde AI-apps opnieuw toestemming vragen.
- **Altijd achter inloggen.** De server staat alleen aan als inloggen aanstaat, zodat de MCP-ingang nooit open staat op een installatie zonder beveiliging.
- README (nl en en) met een hoofdstuk over AI-assistenten, screenshots en de nieuwe variabele; de `.env`-voorbeelden zijn bijgewerkt.
- **De reset-opdracht in de README** laat nu ook alle sessies en AI-apps vervallen (de sessieteller gaat omhoog), zodat een gestolen token niet blijft werken na een reset.
- **Geen netwerktoegang via een recept-URL.** `import_recipe_to_mealie` weigert adressen op een eigen netwerk (`localhost`, privé-IP's, `.local`), zodat een assistent die door een tekst op een webpagina op het verkeerde been wordt gezet Mealie geen interne adressen laat ophalen.
- **Registratie blijft beschikbaar.** Apps die zich registreren maar nooit toestemming krijgen, verdwijnen na een dag (en na 15 minuten als de lijst vol zit), zodat niemand de registratie kan blokkeren.

## v1.3.0

*English summary: MealieShopper now supports multiple users. The first user is the administrator, who links Mealie and Albert Heijn and manages the users. Housemates join through a one-time invitation link and choose their own password or passkey; they can use the basic functions (week menu, recipes, lists, basket) but cannot link or manage anything.*

### Nieuw in 1.3.0

- **Meerdere gebruikers.** De eerste gebruiker is de **beheerder**: die koppelt Mealie en Albert Heijn en beheert de gebruikers. Iedereen die je uitnodigt is een **gebruiker** met de basisfuncties: weekmenu bekijken en in het AH-mandje zetten, recepten zoeken en importeren, AH-lijstjes en -recepten bekijken, lijstjes in het mandje zetten en aantallen aanpassen.
- **Uitnodigen met een link.** Onder Beheer > Gebruikers maak je een link die één keer werkt en na 7 dagen verloopt. De uitgenodigde kiest zelf een gebruikersnaam en een wachtwoord of passkey; de beheerder kent het wachtwoord dus nooit. De link wordt alleen gehasht bewaard en je ziet hem maar één keer.
- **Inlog kwijt? Nieuwe inloglink.** De beheerder maakt per gebruiker een resetlink. De oude inlog (wachtwoord, passkeys en tweestapsverificatie) wordt pas vervangen als de nieuwe is ingesteld.
- **Gebruikers verwijderen** haalt het account en alle inloggegevens weg. De beheerder kan niet worden verwijderd.
- **Eigen inlog per gebruiker.** Wachtwoord, tweestapsverificatie (TOTP en herstelcodes) en passkeys zijn per gebruiker, en een nieuw wachtwoord logt alleen die gebruiker overal uit (de sessieteller staat nu per gebruiker). Een gebruiker ziet en beheert alleen de eigen passkeys en kan de eigen naam niet wijzigen.
- **Koppelen alleen voor de beheerder, ook op de server.** Mealie en AH koppelen en de gebruikersbeheer-API geven een gewone gebruiker een 403. De status van de koppelingen blijft voor iedereen leesbaar. In de interface zien gebruikers de tabs Albert Heijn, Mealie en Gebruikers niet, en bij een ontbrekende Mealie-koppeling staat "Vraag de beheerder om Mealie te koppelen."
- **De reset-opdracht in de README** verwijdert nu ook de andere gebruikers en openstaande uitnodigingen, zodat de installatiehulp na een reset weer schoon begint.
- README (nl en en) uitgebreid met een hoofdstuk over gebruikers en uitnodigen, met screenshots.
- **Fix:** de melding bij een niet-gekoppeld AH-account verwees nog naar de oude tab "AH koppelen"; die noemt nu Beheer > Albert Heijn en de beheerder.
- Bestaande installaties hoeven niets te doen: de bestaande gebruiker blijft de beheerder en behoudt zijn sessies.

## v1.2.0

*English summary: this is the first tagged release. It covers everything from v1.0.0 onwards: a week menu that fills your Albert Heijn basket, recipe search and import into Mealie, saved AH recipes and favourite lists (with local copies, search, editable quantities and a one-press "add the whole list to the basket"), a first-run onboarding with passkey or password (+ optional two-step verification) login, a Dutch/English interface, an About tab, installable PWA and a bilingual README with screenshots.*

### Nieuw in 1.2.0 (sinds v1.0.0)

- **Een lijstje in één keer in je AH-mandje.** In een geopend lijstje zet de knop **In AH-mandje** alle producten met de huidige aantallen in je winkelmandje ([#34](https://github.com/helmerzNL/MealieShopper/pull/34)).
- **Aantallen in een lijstje aanpassen.** Met `−`/`+` of door te typen (1 tot 99). Een aangepast aantal blijft staan na een nieuwe synchronisatie en het AH-aantal staat ernaast (`AH: 2`).
- **Reset-icoon** dat alle aangepaste aantallen terugzet naar de waarden van AH.
- **Synchroniseren als MDI-icoon** in plaats van tekst, per lijstkaart, in het lijstje en bij "Alles synchroniseren".
- **Nieuwe README** in het Nederlands en Engels, met screenshots van desktop en telefoon, de integraties en de koffie-melding onderaan ([#34](https://github.com/helmerzNL/MealieShopper/pull/34)).
- **Het geheim wordt automatisch aangemaakt** (`secret.key` in de data-map) en de `.env`-voorbeelden zijn tweetalig ([#33](https://github.com/helmerzNL/MealieShopper/pull/33)).
- **Fix:** de twijfelmeldingen bij het AH-mandje zijn nu ook in het Engels.
- Bestaande databases krijgen vanzelf de extra kolom voor de AH-aantallen; er is niets te migreren.

### Alle functies vanaf v1.0.0

**Weekmenu naar je AH-mandje**
- Het weekmenu uit Mealie, met de plaatjes uit Mealie ([#30](https://github.com/helmerzNL/MealieShopper/pull/30)), en alle ingrediënten in één keer in je AH-winkelmandje.
- Het aantal verpakkingen volgt de verpakkingsgrootte, binnen 1 tot 99. Is de app er niet zeker van, dan kies je zelf in een controlelijst ([#24](https://github.com/helmerzNL/MealieShopper/pull/24)), met bij elke regel een link naar het product bij AH ([#25](https://github.com/helmerzNL/MealieShopper/pull/25)).

**Recepten**
- Allerhande-recepten zoeken via GraphQL ([#28](https://github.com/helmerzNL/MealieShopper/pull/28)) en importeren in Mealie via de AH-API, zonder 403 ([#22](https://github.com/helmerzNL/MealieShopper/pull/22)).
- Een recept importeren via een URL, met een terugval voor pagina's die Mealie zelf niet kan ophalen.

**AH Items**
- Tabblad met **AH Recepten** (je bewaarde recepten, [#19](https://github.com/helmerzNL/MealieShopper/pull/19) t/m [#21](https://github.com/helmerzNL/MealieShopper/pull/21)) en **AH Lijstjes** (favoriete lijstjes, [#15](https://github.com/helmerzNL/MealieShopper/pull/15) t/m [#17](https://github.com/helmerzNL/MealieShopper/pull/17)).
- Lijstjes en recepten worden lokaal bewaard en blijven zichtbaar als AH niet meewerkt. Zoeken in lijstjes kijkt ook naar de producten erin.
- Responsive kaarten voor telefoon en groot scherm, met een cache voor recepten en plaatjes ([#30](https://github.com/helmerzNL/MealieShopper/pull/30)).

**Koppelen en beveiligen**
- Albert Heijn koppelen via de echte `login.ah.nl`, zodat je passkey en de AH-inlogknop werken ([#2](https://github.com/helmerzNL/MealieShopper/pull/2), [#3](https://github.com/helmerzNL/MealieShopper/pull/3), [#14](https://github.com/helmerzNL/MealieShopper/pull/14)).
- Mealie koppelen in de app onder Beheer > Mealie ([#27](https://github.com/helmerzNL/MealieShopper/pull/27)).
- Onboarding bij de eerste start: inloggen met een passkey (met domeinnaam en HTTPS) of een wachtwoord met optionele tweestapsverificatie, daarna Mealie en AH koppelen ([#31](https://github.com/helmerzNL/MealieShopper/pull/31), [#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Sessies worden ondertekend met een willekeurige, bewaarde sleutel ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).

**Interface**
- Nederlands en Engels, te wisselen met de vlaggetjes (ook in de onboarding) ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Tabblad **Over** met versie en commit, de gebruikte componenten en integraties, en een koffie-verzoek ([#32](https://github.com/helmerzNL/MealieShopper/pull/32)).
- Huisstijl en installeerbare app (PWA) ([#26](https://github.com/helmerzNL/MealieShopper/pull/26)), Material Design Icons in het hoofdmenu ([#29](https://github.com/helmerzNL/MealieShopper/pull/29)) en een zwevende tabbalk op de telefoon ([#28](https://github.com/helmerzNL/MealieShopper/pull/28)).

**Uitrol**
- Docker-image via GitHub Actions naar `ghcr.io/helmerznl/mealieshopper`, voorbeeldbestanden voor Docker Compose en Unraid.

### Fixes en opruiming sinds het begin

- AH-requests volgen het API-profiel van `appie-go` ([#1](https://github.com/helmerzNL/MealieShopper/pull/1)); AH-lijstjes en -recepten via GraphQL omdat de REST-routes 404 geven ([#16](https://github.com/helmerzNL/MealieShopper/pull/16), [#20](https://github.com/helmerzNL/MealieShopper/pull/20), [#28](https://github.com/helmerzNL/MealieShopper/pull/28)).
- Fouten van externe diensten komen als 424 terug, zodat proxies de echte foutmelding laten staan ([#18](https://github.com/helmerzNL/MealieShopper/pull/18)).
- AH-aantallen blijven binnen 1 tot 99, want AH weigert alles daarbuiten.
- Een AH-token of -code wordt aan de serverkant herkend ([#3](https://github.com/helmerzNL/MealieShopper/pull/3)).
- **Verwijderd:** de eerdere Jumbo-koppeling ([#4](https://github.com/helmerzNL/MealieShopper/pull/4)), de AH-website-login met een headless browser en daarmee Selenium en Chromium uit de image ([#9](https://github.com/helmerzNL/MealieShopper/pull/9) t/m [#13](https://github.com/helmerzNL/MealieShopper/pull/13), opgeruimd in [#23](https://github.com/helmerzNL/MealieShopper/pull/23)).

### Issues

Er zijn tot nu toe geen GitHub-issues geopend of openstaand. Alle wijzigingen liepen via pull requests [#1](https://github.com/helmerzNL/MealieShopper/pull/1) t/m [#34](https://github.com/helmerzNL/MealieShopper/pull/34).

### Goed om te weten

- De AH-koppeling gebruikt de **niet-officiële** app-API van AH, die zonder bericht kan veranderen.
- Zonder `secret.key` (of `MEALIESHOPPER_AUTH_SECRET`) zijn opgeslagen koppelingen en sessies onleesbaar. Neem de hele data-map mee in je back-ups.

## v1.0.0

De eerste genummerde versie: het versienummer en het tabblad Over kwamen met [#32](https://github.com/helmerzNL/MealieShopper/pull/32). Alles wat daarvoor is gebouwd staat onder v1.2.0 hierboven, bij "Alle functies vanaf v1.0.0". Voor deze versie is geen aparte GitHub release gemaakt.

## Releases

Bij **elke** nieuwe versie hoort een nieuwe GitHub release:

1. Verhoog `VERSION` (alleen `X.Y.Z`).
2. Voeg bovenaan dit bestand een kopje `## vX.Y.Z` toe met wat er nieuw is: functies, fixes, verwijderde dingen en de pull requests en issues erbij.
3. Merge naar `main`. De workflow `.github/workflows/release.yml` maakt dan de tag `vX.Y.Z` en de GitHub release, met de tekst onder dat kopje. Bestaat de release al, dan gebeurt er niets.

`tests/test_release.py` laat de pull request falen als `VERSION` geen eigen kopje in dit bestand heeft.
