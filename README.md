<img src="docs/design/logo/snug-icon-campari.svg" alt="" width="72" height="72">

# Snug

[![CI](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Snug is a self-hosted data room for early-stage founders. You run it on your own server and use it to share a pitch deck or a due-diligence folder with named investors.

- Each investor gets their own link, and proves they can read the inbox it was sent to.
- You can see who opened what, page by page, and revoke a link at any time.
- Their email address is watermarked on each page they read and on any copy they download.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/screenshots/hero-dark.png">
  <img alt="Behind, the founder's page for a seed deck: six viewers, a median reading time of 8 minutes, and a strip for each investor shaded by the time they spent on each page. In front, the investor's view of page 5, with their email address and the date written across it." src="docs/images/screenshots/hero-light.png">
</picture>

Snug is open source, published by [Negroni Venture Studios](https://negroniventurestudios.com/). Your documents, and the record of who read them, stay on your server and in your own encrypted backups.

> **Status: test builds only.** You can run Snug on your own computer today, and the server setup is written, but it has not yet been tested end to end on a real server. Please do not put real documents in it yet. See [Status](#status).

## Who it is for

Founders raising a seed or Series A round who want more than a shared Drive folder, without paying monthly for a hosted data room or putting their most sensitive documents on someone else's servers.

## What Snug does

- **Documents.** PDFs, read page by page; Excel workbooks (.xlsx), shared for download so investors get the working model; and MP4 videos, watched in the browser with the viewer's watermark over them. Upload new versions; links always show the latest.
- **Rooms.** Collections of documents, with one level of folders. A document can be in any number of rooms.
- **Links.** One per person, to a document or a room, with an optional message from you. Email verification (on by default), optional expiry, downloads (on by default), and immediate revocation.
- **UK-only mode.** A link, or a whole room, can open only for visitors in the UK, Jersey, Guernsey and the Isle of Man, judged by their internet address against a country database kept on your server.
- **An NDA.** One click-through agreement for your instance, versioned, which you can require before a room or a single-document link opens. Snug records who accepted which text, and when.
- **The reader.** Pages are rendered on your server and shown one at a time, watermarked with the reader's email address, the date and the link. A download is the original PDF, stamped with the same details, so its text stays searchable. A video plays in the browser with the same details drawn over it; a workbook's card lists its sheets, and the founder can add a description.
- **Analytics.** Who opened each document, how long they spent on each page (or on each 10 seconds of a video), and how often they downloaded it, on an Overview, each document's page and a People page. Every admin is emailed the first time each link is opened, unless they turn it off. Reading data is deleted 12 months after a link ends, or after a period you set. Everything about your visitors stays on your instance, and Snug does not phone home.
- **An audit log** of every admin action and every viewer access, with the viewer's country, filterable and exportable as CSV.
- **Email** through Google Workspace, as a mailbox on your domain over the Gmail API, or any provider over SMTP. It is set up in Settings in numbered steps, ending with a test email, and can be cleared there with one button. The admin, email and NDA sections of Settings each link to their guide.
- **Admin accounts** with a password and an authenticator app, recovery codes, invitations by email, and an email to every admin whenever the admins change.
- **Running it.** Docker Compose with Caddy for HTTPS; continuous backups to a storage bucket of your own, encrypted on your server with a key only you hold; `snug doctor`, which checks the server and says what to fix, and a status line on the Overview showing the same; and `snug restore`.

You see who opened what on the Overview, and each person's reading on People:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/screenshots/overview-dark.png">
  <img alt="The Overview: everything working, five documents, nine active links, six people who have visited, and the latest visits." src="docs/images/screenshots/overview-light.png" width="49%">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/screenshots/people-dark.png">
  <img alt="The People page: each investor's strip of pages, shaded by time, with their visits, downloads and total time." src="docs/images/screenshots/people-light.png" width="49%">
</picture>

An investor enters a code sent to their inbox, then reads, or downloads a workbook:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/screenshots/code-dark.png">
  <img alt="The page an investor sees first: Fernlea Robotics has shared Seed deck with you, with a box for their email address and a panel listing what the company will see." src="docs/images/screenshots/code-light.png" width="49%">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/screenshots/workbook-dark.png">
  <img alt="A workbook shared with an investor: its description, its sheets, and a button to download it, with their details written into the copy." src="docs/images/screenshots/workbook-light.png" width="49%">
</picture>

[Using Snug](docs/using-snug.md) goes through every page, for you and for the investors you share with.

## What Snug does not do

- **It shows PDFs and videos, and shares Excel workbooks.** Export anything else, such as Word or PowerPoint files, to PDF first, and video to MP4 with H.264. A workbook is not previewed in the browser. Investors download it, and Snug writes their email address, the date and the link into each copy's document properties. A video's watermark is drawn over the player, so a downloaded copy has no mark, and someone determined can save a video they are watching even with downloads turned off.
- **Watermarks deter casual leaks; they cannot stop one.** Anyone can photograph a screen.
- **NDA acceptance is a record, not a signature.** Snug records who accepted which text, and when.
- **UK-only mode is not export control.** It goes by the visitor's internet address, so a VPN gets round it.
- It is not a hosted service, and one instance serves one company.
- No Office or image rendering, comments, e-signatures, single sign-on, investor updates or AI features.

## Status

**Snug is not ready to run for real yet.** Everything planned for version 1 is built and tested, and the container image is published as a test build, `ghcr.io/negroni-venture-studios/snug:edge`. The server setup is written and tested in CI, but has not yet run on a real server. There is no release, so please do not put real documents in a test instance.

Still to come before the first release:

- a full deployment to a DigitalOcean droplet, following the guide alone, and a restore onto a second one;
- a step-by-step guide for a DigitalOcean droplet in London, and a guide to what Snug does and does not protect against.

The order of work in [DESIGN.md](DESIGN.md) has the details.

## Trying it on your own computer

This needs Docker, and runs Snug on `localhost` only, over plain HTTP, which Snug allows for `localhost` and nowhere else.

First make a settings file holding a new secret key, and keep it. Snug signs sessions with the key and protects viewers' one-time codes with it, so a container started with a different key signs everyone out and cancels any code in flight.

```bash
python3 -c 'import secrets; print("SNUG_SECRET_KEY=" + secrets.token_urlsafe(64))' > snug.env
chmod 600 snug.env
```

Then start Snug:

```bash
docker run --detach --name snug --publish 127.0.0.1:8000:8000 \
  --env-file snug.env --env SNUG_DOMAIN=localhost:8000 --env SNUG_HTTPS=false \
  --volume snug-data:/var/lib/snug \
  ghcr.io/negroni-venture-studios/snug:edge
```

Create the first admin, which asks for a password and shows a QR code for your authenticator app:

```bash
docker exec --interactive --tty snug snug createadmin --email you@example.com
```

and sign in at <http://localhost:8000/admin/>. The Overview will show that nothing has been backed up, since backups need the server setup below. Without email settings, Snug writes each email, such as a viewer's code, to a file in the container instead of sending it. To read them:

```bash
docker exec snug sh -c 'cat /var/lib/snug/outbox/*'
```

[The configuration guide](docs/configuration.md) lists every setting.

**Updating.** Your documents and everything else live in the `snug-data` volume, so the container can be replaced at any time. Pull the new image, remove the container, then run the same `docker run` command as above, from the folder holding `snug.env`:

```bash
docker pull ghcr.io/negroni-venture-studios/snug:edge
docker rm --force snug
```

If you started Snug without a `snug.env` file, save its key from the running container first:

```bash
docker inspect snug --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^SNUG_SECRET_KEY=' > snug.env
chmod 600 snug.env
```

**A forgotten password.** With email set up, use "Forgotten your password?" on the sign-in page. Without it, set a new one from the command line; the authenticator app keeps working as before:

```bash
docker exec --interactive --tty snug snug reset-password you@example.com
```

To remove it all: `docker rm --force snug && docker volume rm snug-data`, then delete `snug.env`.

## Running it on a server

[The deployment guide](docs/deployment.md) sets up a fresh Ubuntu server, such as a DigitalOcean droplet, with cloud-init and `sudo snug setup`, which asks for your domain and backup bucket on the server itself; email is set up afterwards in Settings. [The backups guide](docs/backups.md) explains what is backed up, how to check it and how to restore. The email guides explain how to send through [Google Workspace](docs/email-google.md), starting from your first visit to Google Cloud, or through [another provider](docs/email.md). These are untested on a real server until the full deployment above.

## How it is built

Python and Django, with SQLite in WAL mode, server-rendered pages and very little JavaScript. PDFs are read and rendered with pypdfium2 in a separate process, limited in memory and time and confined by Linux Landlock and a filter that blocks network access, so a hostile PDF cannot reach your data. The same process checks Excel workbooks and MP4 videos, with the standard library alone; it never decodes a video, which the viewer's browser plays. Snug is one container image, run beside Caddy, Litestream and rclone. [How Snug works](docs/architecture.md) explains the code, with diagrams, and [DESIGN.md](DESIGN.md) the decisions behind it.

UK-only mode uses [IP geolocation by DB-IP](https://db-ip.com), licensed CC BY 4.0, which your server downloads once a month.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up, test and propose changes.
- Please report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).
- [AGENTS.md](AGENTS.md) holds the working rules for AI coding agents, and people will find it useful too.

## Licence

MIT; see [LICENSE](LICENSE). The Snug name and icon identify this project; if you ship a fork as your own product, please see [TRADEMARKS.md](TRADEMARKS.md).
