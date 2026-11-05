<img src="docs/design/logo/snug-icon-campari.svg" alt="" width="72" height="72">

# Snug

[![CI](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml/badge.svg)](https://github.com/Negroni-Venture-Studios/snug/actions/workflows/codeql.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Snug is a self-hosted data room for early-stage founders. You run it on your own server and use it to share a pitch deck or a due-diligence folder with named investors.

- Each investor gets their own link, and proves they can read the inbox it was sent to.
- You can see who opened what, page by page, and revoke a link at any time.
- Every page carries the reader's email address as a watermark.

Snug is open source, published by [Negroni Venture Studios](https://negroniventurestudios.com/). Negroni does not host it. You own your instance and your data, and Negroni never sees either.

> **Status: early development.** There is nothing to install yet. The plan and the decisions behind it are in [DESIGN.md](DESIGN.md).

## Who it is for

Founders raising a seed or Series A round who want more than a shared Drive folder, without paying monthly for a hosted data room or putting their most sensitive documents on someone else's servers.

## What Snug does

- **Documents.** PDF only. Upload new versions; links always show the latest version.
- **Rooms.** Collections of documents, with one level of folders and an optional NDA click-through.
- **Links.** One per person. Optional expiry, email verification (on by default), downloads (off by default), and immediate revocation.
- **The reader.** Pages are rendered on your server and shown one at a time, watermarked with the reader's email address, the date and the link. The PDF itself never leaves your server unless you allow downloads.
- **Analytics.** Who opened each document, how far they got, and how long they spent on each page. Nothing is sent anywhere outside your instance. Snug does not phone home.
- **An audit log** of every admin action and every viewer access, exportable as CSV.
- **UK-only mode**, per link or room, using a local GeoIP database.

## What Snug does not do

- **It only shows PDFs.** Export decks, spreadsheets and drawings to PDF first.
- **Watermarks deter casual leaks; they cannot stop one.** Nothing stops someone photographing a screen.
- **UK-only mode can be bypassed with a VPN.** It is not, on its own, an export-control measure.
- **NDA acceptance is a record, not a signature.** Snug records who accepted which text, and when.
- It is not a hosted service, and one instance serves one company.
- No Office or image rendering, comments, e-signatures, single sign-on, investor updates or AI features.

## Status

**Snug is not ready to run for real yet.** The application is built and tested, and the container image is published as a test build, `ghcr.io/negroni-venture-studios/snug:edge`. There is no release yet.

Still being built: the production setup (Docker Compose with Caddy for HTTPS and Litestream for continuous, encrypted backups), the `snug doctor` and `snug restore` commands, and the step-by-step guide for a DigitalOcean droplet in London. The order of work in [DESIGN.md](DESIGN.md) lists what remains. Until then, please do not put real documents in a test instance.

## Trying it on your own computer

You need Docker. This runs Snug on `localhost` only, over plain HTTP, which Snug allows for `localhost` and nowhere else.

```bash
docker run --detach --name snug --publish 127.0.0.1:8000:8000 \
  --env SNUG_DOMAIN=localhost --env SNUG_HTTPS=false \
  --env SNUG_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  --volume snug-data:/var/lib/snug \
  ghcr.io/negroni-venture-studios/snug:edge
```

Then create the first admin, which asks for a password and shows a QR code for your authenticator app:

```bash
docker exec --interactive --tty snug snug createadmin --email you@example.com
```

and sign in at <http://localhost:8000/admin/>. Without email settings, emails such as viewers' codes are written to files in the container instead of being sent; read them with `docker exec snug ls /var/lib/snug/outbox`. [docs/configuration.md](docs/configuration.md) lists every setting.

To remove it all: `docker rm --force snug && docker volume rm snug-data`.

## How it is built

Python and Django, with SQLite in WAL mode, server-rendered pages and very little JavaScript. PDFs are rendered with pypdfium2 in a separate, resource-limited process. It ships as one container, alongside Caddy for TLS and Litestream for continuous, encrypted backups. [DESIGN.md](DESIGN.md) records every decision and why.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up, test and propose changes.
- Please report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).
- [AGENTS.md](AGENTS.md) holds the working rules for AI coding agents, and is worth reading for people too.

## Licence

MIT. See [LICENSE](LICENSE). The Snug name and icon identify this project; if you ship a fork as your own product, please see [TRADEMARKS.md](TRADEMARKS.md).
