# Onderzoek: HelloFresh-koppeling (Maaltijdboxen)

Doel: vaststellen of MealieShopper recepten van HelloFresh Nederland kan tonen en naar Mealie kan importeren, als eigen
onderdeel naast de supermarkten. Dit is een spike van 27 oktober 2026: alleen publieke pagina's van
`www.hellofresh.nl` zijn gelezen (een handvol verzoeken, geen login, geen account). Er is niets in de app gebouwd.

## Status

De eerste versie is gebouwd in v2.78.0: hoofdtab **Maaltijdboxen** (`mealieshopper/hellofresh.py`, `static/mealkits.js`,
routes onder `/api/hellofresh/`). Wat erin zit en wat bewust niet:

- Wel: weekmenu per week bladeren, filteren, zoeken in de al bewaarde weken, een recept openen bij HelloFresh of
  importeren in Mealie (JSON-LD naar `mealie.import_via_jsonld`), menu 6 uur bewaard in `hellofresh_menus`, verkeer in
  het auditlog, een eigen User-Agent en een pauze van een seconde tussen verzoeken.
- Niet: een account, een box kiezen of overslaan, allergenen, andere porties dan de standaard van 2, en een aanbiederskeuze
  (sinds v2.79.0 ook Marley Spoon, zie `docs/onderzoek-marleyspoon.md`). `hellofresh_menus` staat bewust in `backup.EXCLUDED_TABLES`.
- De voorwaarden van HelloFresh moeten nog door de eigenaar zijn gelezen voordat dit in een stabiele release komt (zie
  "Wat niet is vastgesteld").

## Kort antwoord

Ja, voor het **lezen van recepten en de weekmenu's is geen account en geen onofficiële API nodig**. De publieke
receptpagina's bevatten een schema.org-`Recipe` (JSON-LD) met ingrediënten, stappen, voedingswaarden en foto. Dat is
dezelfde vorm als `jumbo_recipes.jsonld` en `picnic_recipes.jsonld` nu aan `mealie.import_via_jsonld` geven. Een
koppeling met een HelloFresh-account (box kiezen, week overslaan) is niet onderzocht en blijft buiten de eerste versie.

## Wat is vastgesteld

| Onderdeel | Bevinding |
| --- | --- |
| Officiële API | Geen gevonden. Er zijn alleen onofficiële opties (community-client met magic-link-login, browserautomatisering). |
| `robots.txt` | Receptpagina's, `/menus` en categoriepagina's zijn niet uitgesloten. Wel uitgesloten: `/recipes/search/?q*`, elke URL met `?…page=`, `?…collection=`, `/box-week*` en `/my-account`. |
| Receptpagina | `https://www.hellofresh.nl/recipes/{slug}-{id}` met een id van 24 hex-tekens. Eén verzoek is ongeveer 1,9 MB. Een oude of gekopieerde id verwijst door (301) naar de actuele versie. |
| Structuur | Twee JSON-LD-blokken (`BreadcrumbList` en `Recipe`) en een `__NEXT_DATA__`-blok met het volledige receptobject. |
| JSON-LD `Recipe` | `name`, `description`, `image`, `thumbnailUrl`, `totalTime` (ISO 8601), `recipeYield`, `recipeIngredient`, `recipeInstructions` (`HowToStep`, tekst als HTML), `nutrition`, `recipeCategory`, `recipeCuisine`, `aggregateRating`, `datePublished`. |
| Ingrediënten | Regels als `200 gram Spinazie`, `1 stuk(s) Courgette`, `naar smaak Peper en zout`. Dat loopt tegen `ingredients.py` en `mealie.parse_imported_ingredients` aan; de eenheden `stuk(s)` en `zakje(s)` moeten waarschijnlijk worden genormaliseerd. |
| Porties | De pagina toont de standaard van 2 personen. Het `__NEXT_DATA__`-blok heeft `yields` voor andere aantallen (2, 3 en 4); in dit onderzoek niet verder uitgewerkt. |
| Allergenen | Staan in het `__NEXT_DATA__`-blok (`allergens`, `allergensNew`), niet in de JSON-LD. |
| Weekmenu | `https://www.hellofresh.nl/menus` toont de actieve week (nu `2026-W45`). `https://www.hellofresh.nl/menus/2026-W44` en andere weken zijn ook te openen. Elke week heeft ongeveer 80 recepten; de weken delen geen recepten. |
| Menugegevens | `__NEXT_DATA__` heeft `courses` (lijst) met per gerecht een `recipe` (naam, kop, `websiteUrl`, `prepTime`, `totalTime`, `difficulty`, `cuisines`, `tags`, `label`, `nutrition`, `imagePath`). `ingredients` en `steps` zijn daar leeg: voor de volledige tekst is de receptpagina nodig. |
| Alle recepten | `https://www.hellofresh.nl/sitemap_recipe_pages.xml` (28.798 URL's, 13,6 MB, met `lastmod`). Handig voor een volledige index, te groot voor elke start. |
| Categoriepagina's | Bijvoorbeeld `/recipes/vegetarische-recepten` met ongeveer 90 receptlinks, zonder paginering (paginering via `?page=` is door `robots.txt` uitgesloten). |
| Zoeken | Zoeken bij HelloFresh zelf (`/recipes/search/?q=`) is uitgesloten. Zoeken moet dus lokaal, in een eigen index van menu's en categorieën. |

## Wat niet is vastgesteld

- **Gebruiksvoorwaarden:** de pagina met de algemene voorwaarden gaf `403` op een automatisch verzoek. Een mens moet
  lezen wat erin staat over geautomatiseerd gebruik en over het hergebruiken van receptteksten en foto's, voordat de
  functie in een release komt. Daarom hoort ze bij voorkeur alleen te importeren wat een gebruiker zelf kiest, voor
  eigen gebruik in zijn eigen Mealie, en geen volledige kopie van de site aan te leggen.
- **Stabiliteit:** het `__NEXT_DATA__`-blok is een intern formaat en kan zonder bericht veranderen. De JSON-LD is
  stabieler omdat zoekmachines erop leunen. De ontwerpkeuze is dus JSON-LD voor het recept en `__NEXT_DATA__` alleen
  voor wat daar niet in staat (menu-indeling, allergenen, porties), met een nette foutmelding als het formaat afwijkt.
- **Importeren via URL door Mealie zelf:** niet getest. Mealie kan mogelijk een HelloFresh-URL rechtstreeks lezen
  (`mealie.import_via_url`); dat scheelt code, maar geeft geen controle over ingrediëntregels en porties.
- **Account:** inloggen, box kiezen, bezorgdatum en afbestellen. Daar is geen publieke weg voor. Niet doen zonder aparte
  keuze, omdat het een wachtwoord of token van een betaalabonnement raakt.

## Voorstel voor de eerste versie

1. **Maaltijdboxen > Menu van de week:** de actieve week en een paar weken vooruit en terug, met foto, titel, kop,
   kooktijd en moeilijkheid. Eén verzoek per weekmenu, daarna bewaard met een tijdstip (zoals `ah_lists.py`).
2. **Recept bekijken en importeren:** bij een klik wordt de receptpagina opgehaald, de JSON-LD omgezet en
   met `mealie.import_via_jsonld` in Mealie gezet (zoals `/api/jumbo/recipes/<id>/import`).
3. **Zoeken:** in de lokaal bewaarde menu's; geen verzoeken aan `/recipes/search`.
4. **Zuinig en beleefd:** een eigen `User-Agent` die MealieShopper noemt, een pauze tussen verzoeken, bewaren in plaats van
   herhalen, en geen gebruik van de uitgesloten paden.
5. **Geen login en geen geheim:** er komt dus niets in `backup.SECRET_KEYS`. De bewaarde menu's komen wel in
   `backup.DATA_TABLES` of bewust in `EXCLUDED_TABLES` (het zijn herlaadbare gegevens), met een test in
   `tests/test_backup.py`.

## Vervolg

1. Voorwaarden van HelloFresh lezen en hier vastleggen (door de eigenaar).
2. Besluiten over de naam en de plaats: hoofdtab **Maaltijdboxen**, met een aanbiederskeuze (nu alleen HelloFresh).
3. Daarna een eigen module `mealieshopper/hellofresh.py` naar het voorbeeld van `picnic_recipes.py`, een nieuw tabblad
   in `templates/index.html`, labels in `static/i18n/nl.js` en `en.js`, README's, `VERSION` en `CHANGELOG.md`.
