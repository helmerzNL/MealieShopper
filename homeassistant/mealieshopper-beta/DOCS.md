# MealieShopper

*English below · Nederlands eerst.*

## Nederlands

MealieShopper zet je weekmenu uit [Mealie](https://mealie.io) om in een boodschappenmandje bij Albert Heijn, Picnic of Jumbo. Deze add-on draait dezelfde app als de Docker-versie.

### Installeren

1. Ga in Home Assistant naar **Instellingen > Add-ons > Add-on store**, open het menu rechtsboven (⋮) en kies **Repositories**.
2. Voeg `https://github.com/helmerznl/mealieshopper` toe.
3. Installeer **MealieShopper** en klik op **Starten**.
4. Open `http://<adres van Home Assistant>:5959` (of **Webinterface openen**) en volg de installatie-assistent: die loopt je door de beveiliging en de koppelingen met Mealie en je supermarkten.

Alle opties van de add-on zijn optioneel. Een koppeling die je in de app maakt, gaat voor op dezelfde instelling in de add-on.

### Mealie bereiken

- **Mealie als add-on:** gebruik het adres dat Home Assistant eraan geeft, zoals `http://<hostnaam-van-de-add-on>:9000` (te zien op de infopagina van de Mealie-add-on). Zet dat in de optie *Mealie-adres* of in de app onder **Instellingen > Koppelen**.
- **Mealie elders:** het adres waarop de Home Assistant-machine Mealie bereikt, bijvoorbeeld `http://192.168.1.20:9925`.

### Beveiliging

De webinterface staat op poort 5959 van je netwerk en heeft **geen ingress**: MealieShopper heeft zijn eigen inlog (wachtwoord, tweestapsverificatie, passkeys) en een MCP-server voor AI-assistenten die op een eigen adres bereikbaar moeten zijn. Stel de inlog in de installatie-assistent in en zet de poort niet rechtstreeks op internet. Wil je van buitenaf bij de app, zet er dan een reverse proxy met HTTPS voor; vul daarna het *Publiek adres* in. Passkeys werken alleen met een eigen domeinnaam en HTTPS (de opties *Hostnaam voor passkeys* en *Adressen voor passkeys*); zonder log je in met gebruikersnaam en wachtwoord.

### Gegevens en back-ups

Alles staat in de data-map van de add-on: de database `auth.sqlite3` en `secret.key`. Home Assistant neemt die mee in elke back-up van de add-on en zet ze ook weer terug. Tijdens een back-up wordt de add-on kort gestopt, zodat de database heel blijft. In de app maak je daarnaast een eigen back-upbestand onder **Instellingen > Systeem > Back-up en terugzetten**, bijvoorbeeld om naar een andere installatie te verhuizen.

### Updaten

Home Assistant meldt een nieuwe versie bij de add-on; klik op **Bijwerken**. De database wordt zo nodig vanzelf aangepast. De release-teksten staan op [GitHub](https://github.com/helmerznl/mealieshopper/releases).

### De beta

Voor de ontwikkelversie is er een tweede add-on, **MealieShopper (beta)**, met een eigen data-map en poort 5960, zodat je hem naast de stabiele kunt draaien. Hij staat in de repository met het adres `https://github.com/helmerznl/mealieshopper#beta`. Meld problemen op [GitHub](https://github.com/helmerznl/mealieshopper/issues) met het versienummer uit **Over**.

## English

MealieShopper turns your week menu from [Mealie](https://mealie.io) into a basket at Albert Heijn, Picnic or Jumbo. This add-on runs the same app as the Docker version.

### Installing

1. In Home Assistant go to **Settings > Add-ons > Add-on store**, open the menu at the top right (⋮) and choose **Repositories**.
2. Add `https://github.com/helmerznl/mealieshopper`.
3. Install **MealieShopper** and click **Start**.
4. Open `http://<address of Home Assistant>:5959` (or **Open web UI**) and follow the setup guide: it takes you through security and the links with Mealie and your supermarkets.

All add-on options are optional. A link you make in the app takes precedence over the same setting in the add-on.

### Reaching Mealie

- **Mealie as an add-on:** use the address Home Assistant gives it, such as `http://<host name of the add-on>:9000` (shown on the info page of the Mealie add-on). Put it in the *Mealie address* option or in the app under **Settings > Link**.
- **Mealie elsewhere:** the address at which the Home Assistant machine reaches Mealie, for example `http://192.168.1.20:9925`.

### Security

The web interface is on port 5959 of your network and has **no ingress**: MealieShopper has its own login (password, two-step verification, passkeys) and an MCP server for AI assistants that has to be reachable at its own address. Set up login in the setup guide and do not expose the port directly to the internet. To reach the app from outside, put a reverse proxy with HTTPS in front of it and enter the *Public address*. Passkeys only work with your own domain name and HTTPS (the *Passkey host name* and *Passkey addresses* options); without them you log in with a username and password.

### Data and backups

Everything is in the add-on's data folder: the database `auth.sqlite3` and `secret.key`. Home Assistant includes it in every backup of the add-on and restores it too. The add-on is stopped briefly during a backup so the database stays intact. In the app you can also make a backup file of your own under **Settings > System > Backup and restore**, for example to move to another installation.

### Updating

Home Assistant announces a new version on the add-on; click **Update**. The database is extended automatically when needed. The release notes are on [GitHub](https://github.com/helmerznl/mealieshopper/releases).

### The beta

For the development version there is a second add-on, **MealieShopper (beta)**, with its own data folder and port 5960, so you can run it next to the stable one. It is in the repository with the address `https://github.com/helmerznl/mealieshopper#beta`. Report problems on [GitHub](https://github.com/helmerznl/mealieshopper/issues) with the version number from **About**.
