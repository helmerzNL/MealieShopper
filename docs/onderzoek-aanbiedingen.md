# Onderzoek: betrouwbare aanbiedingen bij recepten

Doel: recepten presenteren op basis van de aanbiedingen van alle winkels (AH, Jumbo, Picnic), **zonder AI-koppeling**
zolang dat kan. De aanbiedingen die nu bij producten staan kloppen vaak niet. Dit document legt vast waarom, wat al
is aangepast en wat nog met echte antwoorden van de winkels moet worden vastgesteld.

## Wat er vóór v2.53.0 aan de hand was

MealieShopper had geen aanbiedingenfunctie. "Aanbieding" betekende alleen: het eerste zoekresultaat van de winkel
voor een ingrediënt heeft een bonusvlag (`isBonus`). Daardoor ging het mis op deze punten:

| Probleem | Waar | Gevolg |
| --- | --- | --- |
| Alleen een ja/nee-vlag, geen actietekst | `ah.py`, `jumbo.py`, `picnic.py` | "2 voor 5", "1+1 gratis" en "2e halve prijs" zijn niet van elkaar te onderscheiden |
| Jumbo gooide de actietekst weg | `jumbo.py` (`_product`) | de voorwaarde van de aanbieding ging verloren |
| AH bewaarde de gewone prijs niet | `ah.py` (`_search_product_dict`) | de korting is niet uit te rekenen |
| Geen begin- of einddatum | alle winkels | een verlopen aanbieding blijft als bonus staan, vooral in opgeslagen lijstjes |
| Productkeuze is het eerste zoekresultaat | `static/app.js`, `app.py` (`cart_preview`) | het product hoeft niet bij het ingrediënt te passen (bijvoorbeeld "kip" en "kipkerrie salade") |
| Prijs kan een voorwaardelijke prijs zijn | alle winkels | de getoonde prijs geldt alleen bij meerdere stuks |
| Ingrediëntnaam is ruw | `static/app.js` | hoeveelheid, eenheid en bijvoeglijke naamwoorden zitten in de zoekterm |

## Wat er in v2.53.0 is aangepast

- `mealieshopper/deals.py`: genormaliseerd aanbiedingsmodel met `parse_mechanic` (Nederlandse actieteksten),
  `effective_unit_price` (prijs per stuk als je de voorwaarde haalt) en `validity` (geldig, verlopen, nog niet begonnen of
  onbekend). Wat niet met zekerheid te lezen is blijft `unknown`.
- AH, Jumbo en Picnic geven bij een product met een aanbieding een extra veld `promo` mee (actietekst, gewone prijs,
  looptijd), naast de bestaande bonusvlag.
- De controlelijst van het weekmenu toont de actie en de looptijd.
- De verkenners van de drie winkels rapporteren hoe een aanbieding eruitziet.

## Wat nog niet vaststaat

De winkels hebben geen gedocumenteerde aanbiedingen-API. De veldnamen in de code zijn afgeleid van wat gangbaar is en
**nog niet met een echt antwoord gecontroleerd**.

| Winkel | Aangenomen velden | Te bevestigen |
| --- | --- | --- |
| AH | `bonusMechanism`, `bonusStartDate`, `bonusEndDate`, `priceBeforeBonus`, `discountLabels` in `product/search/v2` | Bestaan ze? Is `currentPrice` bij een aanbieding met voorwaarde de stukprijs? Is er een lijst of filter met alle actuele bonusproducten? |
| Jumbo | `promotions { tags { text } }` (bestaat), plus mogelijk looptijd en omschrijving | Welke velden heeft een promotie? Staat de looptijd erin? Is er een query voor alle promoties? |
| Picnic | decorator met `PROMO` in het type, `price_ranges`, `price` tegenover `display_price` | Welke tekst staat op het label? Is er een looptijd? Is er een aanbiedingenpagina? |

### Zo stel je het vast

1. Open **Beheer > Winkels** en kies per gekoppelde winkel de verkenner (**AH-routes verkennen**, **Picnic-routes
   verkennen**, **Jumbo-routes verkennen**).
2. Klik op **Verkennen**. Onder "aanbiedingen" staan de velden rond een aanbieding en de actieteksten en datums.
3. Kopieer het rapport en deel het. Het bevat geen namen, adressen, prijzen, producten of id's.

Tijdens de eerste ronde zoekt de verkenner op `kip`, `kaas`, `pasta`, `bier`, `ijs` en `groente`. Staat er in een week
niets in de bonus bij die termen, dan is het rapport leeg; draai het dan later opnieuw.

## Vervolg

1. Veldnamen en looptijd bevestigen met de rapporten en `deals.make_deal` en de `promo`-velden daarop aanpassen.
2. Opslag van aanbiedingen als lokale kopie met tijdstip (zoals `ah_lists.py`), met een eigen ophaalstap als een winkel
   een lijst van alle aanbiedingen geeft; anders per ingrediënt.
3. Strenge matching tussen recept en aanbieding: genormaliseerd ingrediënt, uitsluiting van bereide producten en
   "bevestigd" tegenover "mogelijk". Picnic-recepten met een `productId` per ingrediënt dienen als controle.
4. Een weergave "Recepten met aanbiedingen" en eventueel MCP-tools `list_deals` en `recipes_with_deals`, zodat een externe
   AI-assistent juiste gegevens krijgt zonder dat MealieShopper zelf een API-sleutel nodig heeft.

### Wanneer komt AI in beeld?

Alleen als de deterministische matching na meting onvoldoende nauwkeurig blijkt: een gelabelde set van minstens 50
ingrediënt/aanbieding-paren, met als doel dat "bevestigd" in minstens 95% van de gevallen klopt. Zo niet, dan is een
optionele stap om twijfelgevallen te herordenen het volgende onderzoek. De kern (aanbiedingen ophalen, datums, mechaniek)
heeft geen AI nodig.

## Risico's

- De routes zijn onofficieel en kunnen zonder bericht veranderen; de code houdt daarom rekening met ontbrekende velden.
- Het ophalen van aanbiedingen gebeurt met de eigen koppeling van de gebruiker. Winkelvoorwaarden over geautomatiseerd
  gebruik kunnen daarop van toepassing zijn.
- Andere winkels (Lidl, Plus, Dirk) vallen buiten dit onderzoek; er is geen officiële open bron voor hun aanbiedingen.
