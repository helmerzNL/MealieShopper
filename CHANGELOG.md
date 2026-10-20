# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub.

## v1.5.0

*English summary: MealieShopper can now keep your Albert Heijn in-store receipts (shown like a paper receipt) as a local copy, with the order basket and orders ready behind the scenes and coming soon, so you can browse and search your purchase history without a connection to AH, and your AI assistant can query it. Delivered orders (AH returns the latest 10 closed orders per request) are archived permanently. The administrator can switch each MCP tool off individually. An owner-only explorer reports which of the unofficial AH routes work, to find out how to fetch past orders.*

### Nieuw in 1.5.0

- **Kassabonnen** onder AH Items > Bestellingen, weergegeven zoals een AH-kassabon: aantal, omschrijving, prijs en bedrag, bonusregels, totaal en betaalwijze, op een strook papier (ook leesbaar op een telefoon).
- **Het bestelmandje en de bestellingen komen binnenkort.** AH had een storing en de bestelroutes zijn nog niet beschikbaar, dus de app toont daar een regel dat het binnenkort komt. De code voor mandje, bestellingen, de verkenner en de bijbehorende MCP-tools staat klaar (in `static/orders.js` zet je `ORDERS_COMING_SOON` op `false` om ze te tonen).
- **Bestelmandje, bestellingen en kassabonnen** (backend en MCP) zijn als volgt opgezet. Het bestelmandje is de actieve bestelling bij AH (en dus iets anders dan de boodschappenlijst waar "In AH-mandje" naartoe schrijft; dat staat ook in de interface). Open en afgeronde bestellingen komen uit AH; kassabonnen zijn je aankopen in de winkel.
- **Alles staat in een lokale kopie.** Bekijken en zoeken gaan uit die kopie en vragen nooit iets aan AH, dus de bestelhistorie is ook te zien als AH niet bereikbaar is. Alleen het synchroniseer-icoon praat met AH; mislukt dat, dan blijft de kopie staan en zie je de fout.
- **Slim synchroniseren.** Een volgende synchronisatie haalt alleen op wat nieuw is. Het bestelmandje is een momentopname. Een open bestelling wordt bijgewerkt zolang hij open is. Afgeronde bestellingen haalt MealieShopper ook op (AH geeft er per keer de laatste 10); wat is afgeleverd bewaart hij **voor altijd** en geannuleerde bestellingen slaat hij over. Van maximaal 15 bestellingen per keer komen de producten binnen; de rest volgt bij de volgende synchronisatie. Kassabonnen komen er in porties van 40 bij, zodat een synchronisatie kort blijft.
- **Zoeken in al je aankopen**: wanneer en hoe vaak kocht ik iets, in bestellingen en op kassabonnen, met het detail van elke bestelling en bon.
- **AI-assistenten** kunnen dit bevragen met zes nieuwe tools (alleen lezen, uit de lokale kopie, met het tijdstip van de laatste synchronisatie): `get_ah_basket`, `list_ah_orders`, `get_ah_order`, `list_ah_receipts`, `get_ah_receipt` en `search_ah_purchases`. Het toestemmingsscherm noemt dit.
- **Tools afzonderlijk uitzetten.** De beheerder zet bij AI-apps elke MCP-tool los aan of uit, voor alle gebruikers. Een uitgezette tool verdwijnt uit het overzicht van de assistent en wordt geweigerd als hij toch wordt aangeroepen; andere gebruikers zien in de lijst dat hij is uitgezet.
- **Privacy:** het afleveradres wordt niet opgehaald en niet bewaard, alleen datum en tijdvak. Bestellingen en kassabonnen horen bij het gedeelde AH-account en zijn voor alle gebruikers te zien, net als de lijstjes.
- **AH-routes verkennen** (alleen de beheerder, onder Beheer > Albert Heijn): roept de onofficiële leesroutes aan en meldt welke werken en welke velden erin zitten, ook welke statussen AH voor bestellingen kent. Er wordt niets gewijzigd of bewaard en het rapport bevat geen namen, adressen, prijzen of id's. Een lijst in het rapport toont de samenvoeging van alle elementen. Daarmee zoeken we uit hoe je verder terug komt dan de laatste 10 afgeleverde bestellingen.
- **Leeg bestelmandje is geen fout.** Zonder actieve bestelling antwoordt AH met een 404; dat geldt nu als een leeg mandje.
- Alleen de laatste 10 afgeronde bestellingen zijn per keer op te halen. Hoe je verder terug komt, zoekt de verkenner nu uit (argumenten en statussen van AH, en de inhoud van een afgeleverde bestelling).

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
