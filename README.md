<img src="docs/design/logo/snug-icon-campari.svg" alt="" width="72" height="72">

# Snug

[![CI](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Snug is a self-hosted data room for early-stage founders. You run it on your own server and use it to share a pitch deck or a due-diligence folder with named investors.

- Each investor gets their own link, and proves they can read the inbox it was sent to.
- You can see who opened what, page by page, and revoke a link at any time.
- Every page shows the reader's email address as a watermark, and every download is stamped with it.

Snug is open source, published by [Negroni Venture Studios](https://negroniventurestudios.com/). Negroni does not host it. You own your instance and your data, and Negroni never sees either.

> **Status: test builds only.** You can run Snug on your own computer today, but it is not ready for real documents yet. See [Status](#status).

## Who it is for

Founders raising a seed or Series A round who want more than a shared Drive folder, without paying monthly for a hosted data room or putting their most sensitive documents on someone else's servers.

## What Snug does

- **Documents.** PDF only. Upload new versions; links always show the latest.
- **Rooms.** Collections of documents, with one level of folders. A document can be in any number of rooms.
- **Links.** One per person, to a document or a room. Email verification (on by default), optional expiry, downloads (on by default), and immediate revocation.
- **An NDA.** One click-through agreement for your instance, versioned, which a room or a single-document link can ask for first. Snug records who accepted which text, and when.
- **The reader.** Pages are rendered on your server and shown one at a time, watermarked with the reader's email address, the date and the link. A download is the original PDF, stamped with the same details, so its text stays searchable.
- **Analytics.** Who opened each document, how long they spent on each page, and how often they downloaded it, on an Overview, each document's page and a People page. Reading data is deleted 12 months after a link ends, or after a period you set. Nothing is sent anywhere outside your instance, and Snug does not phone home.
- **An audit log** of every admin action and every viewer access, filterable and exportable as CSV.
- **Admin accounts** with a password and an authenticator app, recovery codes, invitations by email, and an email to every admin whenever the admins change.

## What Snug does not do

- **It only shows PDFs.** Export decks, spreadsheets and drawings to PDF first.
- **Watermarks deter casual leaks; they cannot stop one.** Nothing stops someone photographing a screen.
- **NDA acceptance is a record, not a signature.** Snug records who accepted which text, and when.
- It is not a hosted service, and one instance serves one company.
- No Office or image rendering, comments, e-signatures, single sign-on, investor updates or AI features.

## Status

**Snug is not ready to run for real yet.** The application is built and tested, and the container image is published as a test build, `ghcr.io/negroni-venture-studios/snug:edge`. There is no release yet, so please do not put real documents in a test instance.

Still to come:

- the production setup: Docker Compose with Caddy for HTTPS, and Litestream for continuous backups encrypted with a key only you hold;
- `snug doctor`, which checks a server's setup, and `snug restore`;
- UK-only mode, per link or room, and an email to admins when someone first opens a link;
- a step-by-step guide for a DigitalOcean droplet in London, and a guide to using Snug.

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

and sign in at <http://localhost:8000/admin/>. Without email settings, Snug writes each email, such as a viewer's code, to a file in the container instead of sending it. To read them:

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

## How it is built

Python and Django, with SQLite in WAL mode, server-rendered pages and very little JavaScript. PDFs are read and rendered with pypdfium2 in a separate process, limited in memory and time and confined by Linux Landlock and a filter that blocks network access, so a hostile PDF cannot reach your data. Snug ships as one container image. [DESIGN.md](DESIGN.md) records every decision and why.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up, test and propose changes.
- Please report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).
- [AGENTS.md](AGENTS.md) holds the working rules for AI coding agents, and is worth reading for people too.

## Licence

MIT; see [LICENSE](LICENSE). The Snug name and icon identify this project; if you ship a fork as your own product, please see [TRADEMARKS.md](TRADEMARKS.md).
