# Changelog

Elke release heeft hier een eigen kopje `## vX.Y.Z` en wordt bij het mergen naar `main` automatisch een GitHub release (zie [Releases](#releases)). De tekst onder het kopje is de release-tekst op GitHub.

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
