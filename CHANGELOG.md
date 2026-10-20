# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub.

## v1.4.0

*English summary: MealieShopper now has an MCP server, so you can ask Claude, ChatGPT or GitHub Copilot about your week menu, recipes and AH lists and let them import a recipe into Mealie. Login is OAuth 2.1 with PKCE; every token belongs to the user who gave consent. There is deliberately no tool that fills the AH basket or deletes anything.*

### Nieuw in 1.4.0

- **MCP-server** op `<adres>/mcp` (Streamable HTTP, zonder sessies of streams), zodat je Claude, ChatGPT of Copilot je weekmenu, recepten en lijstjes kunt laten bekijken en een recept in Mealie kunt laten importeren. Tools: `get_week_menu`, `search_allerhande_recipes`, `list_saved_ah_recipes`, `list_ah_lists`, `get_ah_list`, `search_ah_list_items`, `search_ah_products` en `import_recipe_to_mealie`. Er is bewust **geen** tool die het AH-mandje vult of iets verwijdert; tools zijn gemarkeerd als alleen-lezen of (de import) als aanmakend.
- **Inloggen met OAuth 2.1** voor alle assistenten: metadata volgens RFC 9728 en RFC 8414, dynamic client registration (alleen publieke clients, alleen https-, loopback- of app-redirects), verplichte PKCE (S256), een toestemmingsscherm in het Nederlands en Engels, en intrekken via `/oauth/revoke`.
- **Per gebruiker.** Elke code en elk token hoort bij de gebruiker die toestemming gaf en geeft alleen die rechten. Een nieuw wachtwoord, uitgezette tweestapsverificatie, een nieuwe inloglink of het verwijderen van de gebruiker maakt zijn of haar tokens ongeldig.
- **Veilig bewaard.** Tokens staan alleen als hash in de database. Toegangstokens gelden een uur; verversingstokens 30 dagen en worden bij elk gebruik vervangen. Een verversingstoken of code die twee keer wordt gebruikt, trekt de hele reeks in.
- **Beheer > AI-apps** voor elke gebruiker: het adres van de MCP-server met een kopieerknop, uitleg voor Claude, Claude Code, ChatGPT en Copilot, wat de assistent kan, en de gekoppelde apps met een knop om toegang in te trekken. Bij een adres zonder https volgt een waarschuwing.
- **Aan of uit.** De server staat alleen aan als inloggen aanstaat, zodat de MCP-ingang nooit open staat op een installatie zonder beveiliging. Uitzetten kan met `MEALIESHOPPER_MCP=false`.
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
