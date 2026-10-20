<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="static/brand/wordmark-white.svg">
  <img src="static/brand/wordmark.svg" alt="MealieShopper" width="360">
</picture>

**From recipe to sorted.**
Your week menu from [Mealie](https://mealie.io) in your [Albert Heijn](https://www.ah.nl) basket, plus searching, saving and importing recipes.

[Nederlands](README.md) · **English**

<img src="docs/screenshots/en/plan.png" alt="MealieShopper on a computer and a phone: the week menu with pictures from Mealie" width="900">

</div>

## Contents

- [What can it do?](#what-can-it-do)
- [What it looks like](#what-it-looks-like)
- [Quick start](#quick-start)
- [First start](#first-start)
- [Usage](#usage)
- [Linking Mealie and Albert Heijn](#linking-mealie-and-albert-heijn)
- [Security](#security)
- [Users and invitations](#users-and-invitations)
- [AI assistants (MCP)](#ai-assistants-mcp)
- [Configuration](#configuration)
- [Data and backups](#data-and-backups)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Integrations and components](#integrations-and-components)
- [A coffee?](#a-coffee)

## What can it do?

- **Week menu to groceries.** The week menu from Mealie, with the pictures of your recipes. One button looks up the best AH products for every ingredient, works out how many packs you need and puts them in your AH basket. If the app is unsure, you check that line yourself first.
- **Search and import recipes.** Search the Allerhande recipes and put them in Mealie with one click, or import a recipe from a link.
- **Your AH account in MealieShopper.** Your saved recipes and your favourite lists, kept as a copy of their own so they keep working when AH is unavailable. Searching your recipes and lists is instant.
- **An app on your phone.** Install MealieShopper as an app (PWA). On a phone you get a glass menu bar at the bottom, like a real app.
- **Secure login.** A passkey (with a domain name and HTTPS), or a username and password with optional two-step verification.
- **Multiple users.** You are the administrator and invite housemates with a link. They use the basic functions; only you link Mealie and Albert Heijn and manage the users.
- **AI assistants.** An MCP server, so you can let Claude, ChatGPT or Copilot look at your week menu, recipes and lists and import a recipe into Mealie. OAuth login, with your own account.
- **Dutch and English.** Switch with the flags; the app remembers your choice.
- **You stay in control.** Everything runs in your own container; your data lives in a SQLite file in your own data folder.

## What it looks like

Every image shows the same page on a computer and on a phone. The recipes and products are sample data.

### Week menu and basket

Load your week menu from Mealie. The number of packs is calculated with the pack size from AH, and lines the app is not sure about are marked so you check them before the basket is filled.

<img src="docs/screenshots/en/cart.png" alt="Check your basket: per ingredient the product and the number of packs, with one marked line" width="900">

### Find recipes

<img src="docs/screenshots/en/search.png" alt="Searching Allerhande recipes, with cards and an import button" width="900">

### AH Items: your saved recipes

Your saved Allerhande recipes live in MealieShopper, at least two side by side on a phone. Search your recipes at the top.

<img src="docs/screenshots/en/recipes.png" alt="My saved recipes with a search bar and cards with an import button" width="900">

### AH Items: your lists

Your favourite lists as cards. Sync them to MealieShopper, search by name or by product, and open a list in the same place. In an opened list you put the whole list in your basket with one press on **Add to AH basket**, and you change the quantity per product.

<img src="docs/screenshots/en/lists.png" alt="AH lists as cards, with a search bar and a sync button per list" width="900">

<img src="docs/screenshots/en/list.png" alt="An opened list with adjustable quantities, the Add to AH basket button and the reset and sync icons" width="900">

## Quick start

You need a computer or NAS with Docker. You don't have to configure anything: on the first start MealieShopper guides you through security and the links itself.

### Docker Compose (for example Unraid)

1. Create a folder for your data, for example `/mnt/user/appdata/mealieshopper`.
2. Use the included [`docker-compose.yml`](docker-compose.yml). It publishes the app on port `5959` and keeps your data in `/mnt/user/appdata/mealieshopper`.
3. Want to set something? Copy [`.env.unraid.example`](.env.unraid.example) to `.env.unraid` and change what you need. The file is optional (Docker Compose 2.24 or newer).
4. Start:

   ```bash
   docker compose up -d
   ```

5. Open `http://<address of your server>:5959` and follow the setup guide.

The image is at `ghcr.io/helmerznl/mealieshopper:latest`.

### Docker

```bash
docker run -d --name mealieshopper -p 8000:8000 \
  -v /mnt/user/appdata/mealieshopper:/data \
  ghcr.io/helmerznl/mealieshopper:latest
```

Then open `http://localhost:8000`. To build it yourself: `docker build -t mealieshopper .`.

### Run locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .\.venv\Scripts\pip
.venv/bin/python run.py                       # Windows: .\.venv\Scripts\python
```

The app listens on `http://localhost:8000`. See [`.env.local.example`](.env.local.example) for the options.

## First start

On a new installation MealieShopper opens a four-step setup guide. An existing installation (with a passkey or password) does not see it.

**1. Security.** The app looks at how you open it.

| You open MealieShopper through | What you can choose |
|---|---|
| a domain name with HTTPS | a **passkey** (recommended), or a username and password |
| an IP address, `localhost`, a `.local` name or without HTTPS | a username and password, or deliberately **no security** (with a warning); passkeys don't work there |

After the password you can turn on **two-step verification** with an authenticator app. That is optional, just like the password itself. You get 8 recovery codes that you see only once.

<img src="docs/screenshots/en/ob-choose.png" alt="Setup guide, step 1: choosing security" width="900">

<img src="docs/screenshots/en/ob-mfa.png" alt="Setting up two-step verification with a QR code" width="900">

**2. Link Mealie.** Enter the address of your Mealie and an API token; MealieShopper checks them with Mealie before anything is saved.

<img src="docs/screenshots/en/ob-mealie.png" alt="Setup guide, step 2: linking Mealie with address and token" width="900">

**3. Link Albert Heijn.** Instructions for logging in through the browser console, with the login button and the paste field right in the step. This only works on a computer; you can skip the step and do it later.

<img src="docs/screenshots/en/ob-ah.png" alt="Setup guide, step 3: linking Albert Heijn" width="900">

**4. Done.** An overview of what is set up. If a link is missing, a button takes you back to that step.

<img src="docs/screenshots/en/ob-done.png" alt="Setup guide, step 4: overview" width="900">

## Usage

### Week menu to the AH basket

`Load week menu` fetches the week from Mealie, with the pictures. `Add to Albert Heijn basket` looks up the best five products per ingredient and works out how many packs you need, using the pack size AH gives with the product: 6 eggs with "10 pieces" is one box, 500 g with "250 g" is two, and "2 tbsp" of oil is one bottle. The number is always 1 to 99.

If the app is unsure somewhere (unknown pack size, weight against pieces, or more than 10 packs), a check list appears. There you choose the product and the number of packs per line, tick what should go in and confirm. If there is no doubt anywhere, the basket is filled right away. Next to every product is a link to the product at AH, so you can check that it is right.

MealieShopper fetches the pictures from Mealie itself with your API token and keeps them in the database. That way it also works when your Mealie can't be reached directly from your phone, and next time nothing is asked of Mealie.

### Searching and importing recipes

Under `Find recipes` search Allerhande (up to the first 2,000 hits) and choose `Import`, or paste an Allerhande link or another recipe site under `Import from URL`. Importing fetches the recipe data through the AH API and hands it to Mealie as a schema.org recipe, because AH blocks its web pages for servers.

### AH Items

- **AH Recipes**: your saved Allerhande recipes. `Fetch from AH` (afterwards `Refresh`) fetches them all and keeps them in MealieShopper. The tab then shows the local copy and no longer calls AH until you refresh yourself; if a refresh fails, the previous copy stays. Search your recipes at the top.
- **AH Lists**: your favourite lists. With the sync icon (per list, or at the top for all lists) MealieShopper keeps a copy, including product name, brand, size, price and picture. In a synced list you change the quantities with the `−` and `+` buttons or by typing the number. Syncing adds new items and removes what is gone at AH, but leaves a quantity you changed alone (the message says how many changed quantities were kept). The original quantity from AH is shown next to it as `AH: 2`. The icon with the counter-clockwise arrow resets all changed quantities to the values from AH, after you confirm a warning. `Add to AH basket` puts the whole list in your AH basket with the quantities as they are now. The search field filters on the name of the list and on the products in synced lists. If the AH link doesn't work, the synced lists stay visible with a notice.

### Installing as an app

MealieShopper is a PWA: you can install it and it then opens without a browser bar. On a phone you then get a floating, glass menu bar at the bottom with six items.

- **iPhone and iPad (Safari):** Share, then `Add to Home Screen`.
- **Android (Chrome):** menu, then `Install app`.
- **Windows and macOS (Edge or Chrome):** the install icon in the address bar.

Installing only works over HTTPS (except on `localhost`). To look at the menu bar without installing, open `?tabbar=1` after the address on your phone (`?tabbar=0` turns it off again).

### Language

With the flags (top right, on the login page and in the setup guide) you switch between Dutch and English. The choice is kept in your browser; without a choice the app follows your browser language. What Mealie and Albert Heijn return themselves (recipe names, product names and their error messages) stays as they send it.

## Linking Mealie and Albert Heijn

This is also in the setup guide, and you can always do it later under **Settings**.

### Mealie

1. Open Mealie and go to your profile, section **API tokens**.
2. Give the token a name, for example *MealieShopper*, and choose **Generate token**.
3. Copy the token right away: Mealie shows it only once.
4. Enter the address of your Mealie and the token in MealieShopper and choose **Link**.

The address must be reachable from where MealieShopper runs, not necessarily from your phone. If they run in the same Docker environment it can be an internal name such as `http://mealie:9000`. The token is stored encrypted and never shown again. A link you make in the app takes precedence over `MEALIE_URL` and `MEALIE_API_TOKEN` from the environment.

### Albert Heijn

AH has no official link. MealieShopper logs in like the AH app, in your own browser (on a computer):

1. Choose `Log in to Albert Heijn`; a new tab opens.
2. In that tab, open the developer console (`F12`) **before** you log in.
3. Log in with your passkey or password. The page then seems to hang, but the console shows a red line with `appie://login-exit?code=...`.
4. Paste that line in MealieShopper and choose `Link`. The code works only once and expires quickly; a message about an expired or used code means: log in again.

With `Check link` you see whether the stored token still works. AH may replace the token on every refresh; MealieShopper always keeps the newest. So run one instance and keep the data folder on persistent storage.

## Security

<img src="docs/screenshots/en/login.png" alt="The login page with username and password" width="900">

- **Passkey** (domain name with HTTPS): log in with your fingerprint, face or screen lock. Add extra passkeys under Settings, Security; the last passkey can only be removed if there is a password.
- **Password**: at least 10 characters, hashed with scrypt and never stored or logged. After 5 failed attempts per IP address (or 25 in total) logging in is blocked for 15 minutes.
- **Two-step verification** (TOTP, when logging in with a password): the secret is stored encrypted in the database, a code works once, and you get 8 recovery codes (16 characters, only the SHA-256 is in the database). With a passkey you need neither a password nor two-step verification; those blocks are then folded away.
- **Sessions**: an HttpOnly cookie. A new password or disabled two-step verification logs other devices out.

<img src="docs/screenshots/en/security.png" alt="Settings, Security: password, two-step verification and passkeys" width="900">

**The secret.** Without `MEALIESHOPPER_AUTH_SECRET` the app creates a random secret on the first start and keeps it in the data folder (`secret.key`, readable only by the owner). Two separate keys follow from it: one for sessions and one for encrypting your stored links and the two-step secret. If you set `MEALIESHOPPER_AUTH_SECRET` yourself, the key is not on disk next to the database; use a long, fixed, random value (`openssl rand -hex 32`) and don't change it afterwards.

**Good to know**

- As long as no login method is set up, whoever opens the address first can claim the installation. Do the first start on a network you trust, or right after the container is running.
- The IP address used for the block after failed attempts comes from `X-Forwarded-For` of your reverse proxy (one hop). So don't put MealieShopper directly on the internet, but behind a proxy that sets that header itself.
- Passkeys need a domain name and HTTPS. Then set `RP_ID` (the host name only) and `RP_ORIGINS` (the full address), see [`.env.unraid.example`](.env.unraid.example).

## Users and invitations

The first user, the one who goes through the setup wizard, is the **administrator**. They link Mealie and Albert Heijn and manage the users. Everyone you invite is a **user**: they can use the week menu, the recipes and the AH lists, put products in the AH basket and import recipes, but cannot link or manage anything. The AH basket is that of the linked AH account, so Mealie and AH are shared.

<img src="docs/screenshots/en/users.png" alt="Settings, Users: the administrator with two users, and a form to invite someone" width="900">

1. As administrator go to **Settings, Users** and choose **Create invitation** (with or without a name).
2. Copy the link and send it on. You only see it once; the link works once and expires after 7 days.
3. Whoever opens the link chooses a username and a password or a passkey. So you never know their password.

<img src="docs/screenshots/en/invite.png" alt="The page where an invited person creates an account, with a password or a passkey" width="900">

- **Lost their login?** Choose **New login link** for that user. The old login keeps working until the new one is set up, and stops working after that.
- **Remove** deletes the account and all its login details. The administrator cannot be removed.
- Every user has their own login details, two-step verification and passkeys under Settings, Security. A new password only logs that user out.
- Quantities in a list, and the lists themselves, are shared: they belong to the AH account.

## AI assistants (MCP)

MealieShopper has an [MCP](https://modelcontextprotocol.io) server at `<address of MealieShopper>/mcp`. With it you can ask Claude, ChatGPT or GitHub Copilot things like *"what are we eating this week and what's in my Weekly groceries list?"* or *"import this recipe into Mealie"*.

<img src="docs/screenshots/en/ai.png" alt="Settings, AI apps: the address of the MCP server and instructions per assistant" width="900">

**What the assistant can do** (only in your name and with your rights):

| Tool | Does |
|---|---|
| `get_week_menu` | The week menu from Mealie, with the ingredients. |
| `search_allerhande_recipes` | Search recipes at Allerhande. |
| `list_saved_ah_recipes` | Your saved AH recipes, optionally filtered. |
| `list_ah_lists`, `get_ah_list`, `search_ah_list_items` | Your AH lists, the products in them (with your quantities) and where a product is. |
| `search_ah_products` | Search products in the AH range, with price and bonus. |
| `import_recipe_to_mealie` | Import a recipe into Mealie. This creates something new and overwrites nothing. |

There is deliberately **no** tool that fills your AH basket or deletes anything.

**Setting up**

1. As administrator open **Settings, AI apps**. At the top are the settings: the **MCP server on** switch and the **public address**. The app detects that address itself from how you open it; if MealieShopper is behind a reverse proxy, check that it is the public https address and otherwise enter it here. ChatGPT and claude.ai reach the server from outside, so the address must be reachable from the internet, with HTTPS. Forward `/mcp`, `/oauth/*` and `/.well-known/oauth-*` in your reverse proxy (an ordinary proxy does that for the whole site already).
2. As a user open **Settings, AI apps** (the settings are not shown to you), copy the address and follow the instructions for your assistant. You get the consent screen; after **Allow** the assistant is connected.
   - **Claude** (claude.ai, Desktop, phone): Settings, Connectors, *Add custom connector*.
   - **Claude Code:** `claude mcp add --transport http mealieshopper https://your-address/mcp`, then `/mcp` to log in.
   - **ChatGPT:** developer mode on, a new connector with the address and OAuth. Availability depends on your plan.
   - **GitHub Copilot (VS Code):** `{"servers": {"mealieshopper": {"type": "http", "url": "https://your-address/mcp"}}}` in `.vscode/mcp.json`.

<img src="docs/screenshots/en/consent.png" alt="The consent screen: ChatGPT asks for access, with Deny and Allow" width="900">

**Security**

- OAuth 2.1 with PKCE (S256); apps register themselves (dynamic client registration), public clients only, and only https, loopback or app redirects.
- Every token belongs to **the user who gave consent** and gives only that user's rights. Access tokens last an hour, refresh tokens 30 days and are replaced at every use; a refresh token that is used twice revokes the whole chain. Tokens are stored hashed only.
- A new password, turned-off two-step verification, a new login link or removing the user invalidates that user's tokens. Under **Settings, AI apps** you revoke an app's access yourself.
- The server only runs when login is on. The administrator turns it on or off with the switch; only the administrator can see and change the settings.
- What the assistant reads (recipe names, product names) comes from Mealie and AH; treat it as data, not instructions. That is why the assistant can't delete or buy anything.

## Configuration

Everything is optional. Set the values in your environment or in `.env.unraid` ([`.env.unraid.example`](.env.unraid.example) and [`.env.local.example`](.env.local.example) show everything).

| Variable | Purpose |
|---|---|
| `MEALIESHOPPER_DATA_DIR` | Folder for the database and `secret.key`. `/data` in the Docker image, `./data` by default locally. |
| `MEALIESHOPPER_AUTH_SECRET` (or `JWT_SECRET`) | Your own secret for sessions and encryption. Without it the app creates `secret.key`. |
| `MEALIESHOPPER_PUBLIC_BASE_URL` | The public address (for the preview image, invitation links and the AI connection). Optional: the app detects it itself, and an address the administrator enters under Settings, AI apps takes precedence. |
| `RP_ID`, `RP_ORIGINS` (or `RP_ORIGIN`), `RP_NAME` | Only for passkeys, with a domain name and HTTPS. |
| `MEALIESHOPPER_MCP` | Initial value of the MCP server for AI assistants (on by default, when login is on). The switch under Settings, AI apps takes precedence. |
| `PASSKEY_AUTH_ENABLED` | With `false` login is turned off completely (emergency only). |
| `MEALIE_URL`, `MEALIE_API_TOKEN` | Mealie through the environment; a link made in the app takes precedence. |
| `AH_REFRESH_TOKEN` | Starting value for the AH link; you normally link in the app. |
| `PORT` | Port for `python run.py` (default 8000). |
| `MEALIESHOPPER_VERSION`, `MEALIESHOPPER_COMMIT` | Override the version number and commit shown under About. |

## Data and backups

Everything is in the data folder: `auth.sqlite3` (settings, passkeys, password, copies of your AH lists and recipes, pictures from Mealie) and `secret.key`. **Include the whole folder in your backups.** Without `secret.key` (or your own `MEALIESHOPPER_AUTH_SECRET`) the stored links and sessions can't be read: you link Mealie and Albert Heijn again, and the app shows that as "not linked".

## Updating

```bash
docker compose pull && docker compose up -d
```

With a new version you don't have to do anything; the database is extended automatically when needed. Under About you can see which version and commit is running.

## Troubleshooting

**An `Internal Server Error` right away, or the auth database can't be reached.** Check that the appdata folder exists and is writable, and that your compose has the mount `"/mnt/user/appdata/mealieshopper:/data"`. On Unraid:

```bash
mkdir -p /mnt/user/appdata/mealieshopper
chown -R nobody:users /mnt/user/appdata/mealieshopper
chmod -R ug+rwX /mnt/user/appdata/mealieshopper
```

**The passkey doesn't work.** A passkey needs a domain name and HTTPS (on `localhost` it works too). Open MealieShopper through that address and check `RP_ID` and `RP_ORIGINS`; the setup guide explains why it can't work on an IP address.

**AH: "code expired or used".** The code from the console works only once and expires quickly. Log in again and paste the new line right away.

**"Not linked" although you had linked before.** Then `secret.key` (or your own secret) has changed or is missing; see *Data and backups*. Link again.

**I can't get in any more.** If you lost your password and recovery codes, you can reset the login (your links stay). Optionally stop the container and run:

```bash
docker exec -it mealieshopper python -c "import sqlite3; c = sqlite3.connect('/data/auth.sqlite3'); [c.execute(q) for q in ('DELETE FROM password_credentials', 'DELETE FROM mfa_totp', 'DELETE FROM mfa_recovery_codes', 'DELETE FROM passkey_credentials', 'DELETE FROM invites', \"DELETE FROM users WHERE id <> 'owner'\", 'UPDATE users SET session_epoch = session_epoch + 1', 'DELETE FROM login_attempts', \"DELETE FROM app_settings WHERE key IN ('AUTH_MODE', 'ONBOARDING_DONE', 'ONBOARDING_ACTIVE')\")]; c.commit()"
```

Then open the app: the setup guide appears again and you set up security anew. This also removes the other users (your account as administrator stays); invite them again afterwards.

## Development

```bash
python -m unittest discover -s tests          # all tests
```

- **Structure:** `mealieshopper/` (Flask app: `app.py`, `auth.py`, `auth_password.py`, `users.py`, `onboarding.py`, `mealie.py`, `ah.py`, `mcp.py`, `mcp_http.py`, `oauth.py`, `about.py`, `i18n.py`), `static/` and `templates/` (the interface, no build step), `tests/`.
- **Texts and languages:** the interface texts are in `static/i18n/nl.js` and `en.js`, one text per line. In the HTML the Dutch text is in the page, with `data-i18n="key"`; in the scripts use `t("key", { name })`, `th(...)` (with markup, values escaped) and `tn("key", count)` (plurals). Adding a language means a file `static/i18n/<language>.js`, a flag and the language in `static/i18n.js` and `mealieshopper/i18n.py`. `tests/test_i18n.py` makes sure both dictionaries are equal.
- **Brand:** colours, typography and PWA settings are in [`docs/huisstijl.md`](docs/huisstijl.md) (in Dutch). Rebuild the image files with `python scripts/build_brand.py` (needs Pillow).
- **Version and release:** the version number is in `VERSION` (just `X.Y.Z`); raise it on a release, write a heading for the new version in [`CHANGELOG.md`](CHANGELOG.md) and merge to `main`: the workflow `.github/workflows/release.yml` then creates the tag and the GitHub release with that text. Every version gets its own GitHub release. The commit comes from the build: the Dockerfile has the build argument `GIT_COMMIT`, and the workflow in `.github/workflows/docker.yml` sets it to `github.sha`. By hand: `docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t mealieshopper .`
- **Container registry:** the workflow builds and pushes on every push to `main` (and manually through `workflow_dispatch`) to `ghcr.io/helmerznl/mealieshopper:latest`.
- **Screenshots:** the images in `docs/screenshots/` were made with Playwright against a demo environment with hand-drawn illustrations (no third-party photos), so they are free to use anywhere.

## Integrations and components

MealieShopper is an unofficial tool and is not affiliated with Mealie or Albert Heijn.

**Integrations**

| Integration | What for |
|---|---|
| [Mealie](https://mealie.io) | Your recipes and week menu; imported recipes end up here. Through the API with a token. |
| [Albert Heijn](https://www.ah.nl) | Lists, saved recipes, products and the basket, through the **unofficial** app API of AH. AH may change it without notice; use it for personal use. |
| [Allerhande](https://www.ah.nl/allerhande) | Searching and importing recipes. |
| [WebAuthn](https://www.w3.org/TR/webauthn-2/) | Logging in with a passkey. |
| [TOTP (RFC 6238)](https://www.rfc-editor.org/rfc/rfc6238) | Two-step verification with an authenticator app. |
| [MCP](https://modelcontextprotocol.io) and [OAuth 2.1](https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/) | The link with AI assistants such as Claude, ChatGPT and Copilot. |
| [GitHub Container Registry](https://ghcr.io) | Where the Docker image lives. |

**Components**

| Component | Licence | What for |
|---|---|---|
| [Python](https://www.python.org) | PSF-2.0 | The programming language of the server. |
| [Flask](https://flask.palletsprojects.com) | BSD-3-Clause | The web framework. |
| [Gunicorn](https://gunicorn.org) | MIT | The web server in the container. |
| [PyJWT](https://pyjwt.readthedocs.io) | MIT | Signs sessions. |
| [cryptography](https://cryptography.io) | Apache-2.0 OR BSD-3-Clause | Encrypts stored links and verifies passkeys. |
| [cbor2](https://github.com/agronholm/cbor2) | MIT | Reads passkey data. |
| [Requests](https://requests.readthedocs.io) | Apache-2.0 | Talks to Mealie and Albert Heijn. |
| [SQLite](https://sqlite.org) | Public domain | The database. |
| [Material Design Icons](https://pictogrammers.com/library/mdi/) | Apache-2.0 | The icons. |
| [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) | MIT | Draws the QR code for two-step verification in your browser. |
| [Inter](https://rsms.me/inter/) and [Manrope](https://github.com/sharanda/manrope) | OFL-1.1 | The typefaces. |

You can see the versions of the Python packages in the app under **About**.

## A coffee?

MealieShopper is free. Do you find the app useful? Then I'd appreciate it if you bought me a coffee.

☕ **[buymeacoffee.com/flux76](https://buymeacoffee.com/flux76)**
