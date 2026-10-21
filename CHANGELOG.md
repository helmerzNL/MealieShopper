# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub.

## v1.7.0

*English summary: Picnic now shows the next available delivery slots under the filled basket and through a new read-only MCP tool, `list_picnic_delivery_slots`. The basket also reports what you actually pay and what your membership saves. The Picnic route explorer checks more routes (account, lists, store, suggestions, product page) so the next steps can be built on real answers. Choosing a slot and placing an order remain in the Picnic app.*

### Nieuw in 1.7.0

- **Bezorgmomenten van Picnic.** Na het vullen van je Picnic-mandje laat de planner de eerstvolgende beschikbare bezorgmomenten zien, met de minimale bestelwaarde. Picnic levert die in het mandje-antwoord mee; ze zijn dus live en er wordt niets gekozen of gereserveerd. Het bezorgmoment kies je zelf in de Picnic-app. Nieuwe route: `GET /api/picnic/slots`.
- **Nieuwe MCP-tool, alleen lezen:** `list_picnic_delivery_slots` (beschikbare bezorgmomenten met begin, einde, uiterste bestelmoment en minimale bestelwaarde). Net als de andere Picnic-tools afzonderlijk uit te zetten bij AI-apps.
- **Mandje met echt totaal.** `get_picnic_basket` en `GET /api/picnic/cart` melden nu ook wat je werkelijk betaalt (`checkoutTotal`, met bezorgkosten en statiegeld) en wat je lidmaatschap bespaart (`savings`).
- **Picnic-routes verkennen kijkt verder.** Naast zoeken, mandje en leveringen worden nu ook account, lijstjes, winkel, suggesties en de productpagina aangeroepen. Alleen lezen en zonder persoonlijke gegevens in het rapport. Zo zien we welke uitbreidingen (lijstjes, productinfo) mogelijk zijn.

### Goed om te weten

- Het zoeken, het mandje en de leveringsroutes zijn bevestigd met een echt Picnic-account: de verkenner gaf voor alle vijf routes een antwoord. De vorm van de bezorgmomenten is daaruit afgeleid. De vorm van een levering en van een gevuld mandje is nog niet gezien (het account had geen leveringen en een leeg mandje); meld wat afwijkt.
- Een bezorgmoment kiezen of een bestelling plaatsen kan niet vanuit MealieShopper, en dat blijft zo.

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
