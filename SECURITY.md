# Beveiligingsbeleid · Security policy

*Nederlands eerst · [English below](#english).*

## Nederlands

### Ondersteunde versies

| Versie | Ondersteund |
| ------ | ----------- |
| Nieuwste stabiele release (`vX.Y.Z`, als *Latest* gemarkeerd op de pagina [Releases](https://github.com/helmerzNL/MealieShopper/releases); image-tag `latest`, Home Assistant-add-on **MealieShopper**) | :white_check_mark: |
| Nieuwste beta (pre-release `vX.Y.Z-beta`; image-tag `beta`, Home Assistant-add-on **MealieShopper (beta)**) | :white_check_mark: een fix komt hier eerst |
| Elke oudere release of beta | :x: werk bij naar de nieuwste release |

Een beveiligingsfix komt uit als nieuwe versie. Oudere releases krijgen geen fixes.

### Een kwetsbaarheid melden

**Meld een kwetsbaarheid nooit in een openbaar issue, een discussie of een pull request.**

Meld haar vertrouwelijk: [open een privé-melding (security advisory)](https://github.com/helmerzNL/MealieShopper/security/advisories/new) voor deze repository.

Vermeld zoveel mogelijk van het volgende:

- de versie waarin het zit (zie **Over** in MealieShopper, of de image-tag die je draait);
- het soort probleem (bijvoorbeeld: inloggen omzeilen, injectie, cross-site scripting, gegevens die uitlekken);
- de stappen om het na te doen, of een proof of concept;
- de gevolgen: wat een aanvaller kan, en wat die daarvoor eerst nodig heeft (een account, toegang tot het netwerk, een bepaalde instelling);
- of je weet dat het al wordt misbruikt of al openbaar bekend is.

### Wat je kunt verwachten

| Stap | Wanneer |
| ---- | ------- |
| Bevestiging dat we je melding hebben ontvangen | binnen 3 werkdagen |
| Eerste beoordeling: geaccepteerd of afgewezen, en hoe ernstig | binnen 10 werkdagen |
| Een stand van zaken zolang we eraan werken | minstens elke 14 dagen |
| Fix uitgebracht, ernst **kritiek** of **hoog** | doel: 30 dagen na acceptatie |
| Fix uitgebracht, ernst **midden** of **laag** | doel: 90 dagen na acceptatie, of met de volgende gewone release |

De ernst volgt CVSS, zoals berekend in de advisory op GitHub.

**Wordt de melding geaccepteerd**, dan:

1. bevestigen we het probleem en spreken we in de privé-advisory samen de ernst af;
2. werken we de fix uit, waar mogelijk in de tijdelijke privé-fork van de advisory, en brengen we hem eerst uit in de beta (`vX.Y.Z-beta`) en daarna met de promotie naar `main` in de stabiele release (`vX.Y.Z`);
3. zetten we in de release-tekst wat je moet doen om bij te werken;
4. publiceren we de security advisory (en vragen we waar nodig een CVE aan) zodra de fix is uitgebracht;
5. noemen we je in de advisory, tenzij je liever anoniem blijft.

**Wordt de melding afgewezen**, bijvoorbeeld omdat ze buiten dit beleid valt, niet na te doen is of om bedoeld gedrag gaat, dan leggen we in de advisory uit waarom en sluiten we haar. Denk je dat we het mis hebben, reageer dan gerust met meer informatie.

### Openbaar maken

We werken met gecoördineerde openbaarmaking. Geef ons 90 dagen vanaf je melding, of tot de fix is uitgebracht als dat eerder is, voordat je het probleem openbaar maakt. Hebben we langer nodig, dan zeggen we waarom en spreken we samen een nieuwe datum af.

### Waar dit beleid over gaat

**Valt eronder**

- de broncode in deze repository;
- wat eruit wordt gebouwd: de container-image `ghcr.io/helmerznl/mealieshopper` en de Home Assistant-add-ons **MealieShopper** en **MealieShopper (beta)**.

Er is geen gehoste versie van MealieShopper; elke installatie draait bij de eigenaar zelf.

**Valt er niet onder**

- kwetsbaarheden in Mealie zelf of in de diensten van Albert Heijn, Jumbo, Picnic, Crisp, HelloFresh en Marley Spoon: meld die bij hen. Maakt MealieShopper het erger, bijvoorbeeld doordat een token van zo'n dienst uitlekt, dan valt dat wel onder dit beleid;
- kwetsbaarheden in afhankelijkheden die al openbaar bekend zijn en op een fix van de makers wachten: meld die daar; laat het ons weten als MealieShopper erdoor geraakt wordt op een manier die die melding niet dekt;
- installaties die verkeerd zijn ingericht of afwijken van de beschreven opzet, zoals een installatie zonder HTTPS of rechtstreeks op internet zonder reverse proxy (zie [Privacy en beveiliging](README.md#privacy-en-beveiliging));
- installaties waarin het inloggen bewust uit staat (`PASSKEY_AUTH_ENABLED=false`, of *Verder zonder beveiliging* in de installatiehulp);
- denial of service door de hoeveelheid verkeer, en limieten die alleen vóór de app bestaan;
- social engineering, phishing en fysieke aanvallen;
- meldingen van automatische scanners zonder aangetoonde gevolgen.

### Veilige haven

We ondernemen geen juridische stappen tegen wie te goeder trouw een kwetsbaarheid onderzoekt en volgens dit beleid meldt: wie privacyschendingen, het vernietigen van gegevens en het verstoren van diensten vermijdt, alleen eigen accounts (ook bij de supermarkten) en testgegevens gebruikt, en ons de tijd hierboven geeft om het op te lossen voordat het openbaar wordt.

Er is geen beloning voor gevonden kwetsbaarheden (bug bounty).

### Op de hoogte blijven

Beveiligingsfixes staan in de [release-teksten](https://github.com/helmerzNL/MealieShopper/releases) en in de [security advisories](https://github.com/helmerzNL/MealieShopper/security/advisories) van deze repository. Volg de repository (*Watch → Custom → Releases* en *Security alerts*) om een melding te krijgen.

Bijwerken: `docker compose pull && docker compose up -d`, of in Home Assistant **Bijwerken** bij de add-on. Meer staat in de README onder [Beheer](README.md#beheer).

## English

### Supported versions

| Version | Supported |
| ------- | --------- |
| Latest stable release (`vX.Y.Z`, marked *Latest* on the [Releases](https://github.com/helmerzNL/MealieShopper/releases) page; image tag `latest`, Home Assistant add-on **MealieShopper**) | :white_check_mark: |
| Latest beta (pre-release `vX.Y.Z-beta`; image tag `beta`, Home Assistant add-on **MealieShopper (beta)**) | :white_check_mark: fixed here first |
| Any older release or beta | :x: update to the latest release |

Security fixes are released as a new version. They are not backported to older releases.

### Reporting a vulnerability

**Do not report security vulnerabilities through public GitHub issues, discussions or pull requests.**

Report them privately instead: [open a private security advisory](https://github.com/helmerzNL/MealieShopper/security/advisories/new) for this repository.

Please include as much of the following as you can:

- the affected version (see **About** in MealieShopper, or the image tag you run);
- the type of issue (for example: authentication bypass, injection, cross-site scripting, information disclosure);
- the steps to reproduce it, or a proof of concept;
- the impact: what an attacker can do, and what they need first (an account, network access, a specific configuration);
- whether you know of the issue being exploited or publicly known.

### What to expect

| Step | When |
| ---- | ---- |
| Acknowledgement that we received your report | within 3 working days |
| First assessment: accepted or declined, and the severity | within 10 working days |
| Status update while we work on it | at least every 14 days |
| Fix released, **critical** or **high** severity | target 30 days after acceptance |
| Fix released, **medium** or **low** severity | target 90 days after acceptance, or with the next regular release |

Severity follows CVSS as calculated in the GitHub advisory.

**If the report is accepted**, we:

1. confirm the issue and agree the severity with you in the private advisory;
2. develop the fix, where possible in the advisory's temporary private fork, and ship it in the beta first (`vX.Y.Z-beta`), then in the stable release (`vX.Y.Z`) when `beta` is promoted to `main`;
3. put what you need to do to update in the release notes;
4. publish the security advisory (and request a CVE where appropriate) once the fix is released;
5. credit you in the advisory, unless you prefer to stay anonymous.

**If the report is declined**, for example because it is out of scope, cannot be reproduced or is intended behaviour, we explain why in the advisory and close it. You are welcome to reply with more information if you think we got it wrong.

### Disclosure policy

We follow coordinated disclosure. Please give us 90 days from your report, or until a fix is released if that is sooner, before you disclose the issue publicly. If we need longer, we will tell you why and agree a new date with you.

### Scope

**In scope**

- the source code in this repository;
- the released artifacts built from it: the container image `ghcr.io/helmerznl/mealieshopper` and the Home Assistant add-ons **MealieShopper** and **MealieShopper (beta)**.

There is no hosted instance of MealieShopper; every installation is run by its owner.

**Out of scope**

- vulnerabilities in Mealie itself or in the services of Albert Heijn, Jumbo, Picnic, Crisp, HelloFresh and Marley Spoon: please report those to them. If MealieShopper makes it worse, for example by leaking a token for such a service, that is in scope;
- vulnerabilities in third-party dependencies that are already publicly known and are waiting for an upstream fix: please report those upstream; tell us if MealieShopper is affected in a way the upstream report does not cover;
- installations that are misconfigured or deviate from the documented setup, such as an installation without HTTPS or exposed directly to the internet without a reverse proxy (see [Privacy and security](README.en.md#privacy-and-security));
- installations where login is deliberately switched off (`PASSKEY_AUTH_ENABLED=false`, or *Continue without security* in the setup guide);
- denial of service through traffic volume, and rate limits that exist only in front of the app;
- social engineering, phishing and physical attacks;
- reports from automated scanners without a demonstrated impact.

### Safe harbour

We will not take legal action against anyone who, in good faith, researches and reports a vulnerability under this policy: who avoids privacy violations, data destruction and service disruption, uses only their own accounts (at the supermarkets too) and test data, and gives us the time above to fix the issue before disclosing it.

There is no bug bounty programme.

### Staying up to date

Security fixes are announced in the [release notes](https://github.com/helmerzNL/MealieShopper/releases) and in the [security advisories](https://github.com/helmerzNL/MealieShopper/security/advisories) of this repository. Watch the repository (*Watch → Custom → Releases* and *Security alerts*) to be notified.

To update: `docker compose pull && docker compose up -d`, or **Update** on the add-on in Home Assistant. More is in the README under [Running it](README.en.md#running-it).
