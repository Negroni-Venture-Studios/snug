# Changelog

All notable changes to Snug are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Snug uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Link expiry dates are shown as the last day the link works. The links list and the link email showed the day after.

### Added

- The NDA: one agreement for the instance, written in Settings and versioned. A room, or a link to a single document, can ask for it before anything is shown. The viewer gives their name and ticks to accept; Snug records the name, email, link, time, IP address, browser and a fingerprint of the exact text, and asks again when the agreement changes. `docs/nda.md` says plainly that this is a record of acceptance, not a signature.

- Rooms, in the admin interface: create and rename a room, add folders (one level), and add, move and remove documents. A document can be in any number of rooms. Every change is recorded in the audit log.
- Sharing a room: one link opens a page listing the room's documents under their folders, each opening in the reader with its own downloads and reading time. Removing a document from the room stops the link reaching it at once.
- A People page: everyone who has opened a document, most recent first, with what each of them read in the same page strips.

- How a document was read, on its page: viewers, visits, median reading time and how many read every page, then one grid with the average time per page above a strip for each viewer, shaded by time on each page, with their visits, total time and when they were last seen.

- Reading time: each page a viewer is shown, and how long it was in view, measured by Snug from heartbeats sent only while the tab is visible. Visits end after 30 minutes away. Each new visit is recorded in the audit log. Nothing is sent anywhere outside the instance.
- An optional link to your own privacy policy, set in Settings and shown to viewers beside the statement of what Snug records about them.

- The reader: once a viewer is in, each page is shown as an image, one at a time, with Previous, Next, the arrow keys and a page picker. Every page is watermarked for that viewer on each request, with their email address and the date across the page and a footer naming them, the date and the link. Watermarks are stamped in the same confined worker that renders pages.
- Downloads: when a link allows them (the default), the reader offers the original PDF, with a line naming the viewer, the date and the link on every page and the same details in the file's properties. The text stays selectable and searchable. Each download is recorded in the audit log with a fingerprint of the exact file sent.

- A Settings page listing the admins, and inviting new ones. An invite is an emailed link that works once, for 72 hours, where the new admin chooses a password and sets up their authenticator app. Inviting asks for a fresh code from your own authenticator, and every admin is emailed when someone is invited.
- Removing an admin, or cancelling an invite, and resetting another admin's second factor, from Settings. Both ask for a fresh code from your authenticator and email every admin. You cannot remove yourself, and the last admin can never be removed.

- Links, in the admin interface:
  - a link for one person to one document, with an optional note, an expiry date, and choices for verification and downloads. Downloads are on by default, and can be turned off for a link that must be view-only;
  - emailed to the recipient, or shown once to copy and send yourself, with a copy button;
  - a Links page listing every link, and the links for each document on its page;
  - revoking a link, and replacing it with a new one, which stops the old address working at once.

  Link tokens are stored only as hashes, so a copy of the database cannot be used to open them. Every link action is recorded in the audit log.
- The viewer's first pages, at each link's address:
  - a landing page naming the document, with an *About this link* panel;
  - for links that need verification, an emailed six-digit code, with clear messages for a wrong address, a wrong code and tries left;
  - for links that do not, a single *Open* button, so an email scanner visiting the link starts nothing;
  - one page for unknown, expired and revoked links alike.

  Code requests and checks are rate limited per address, per link and per inbox, and every step is recorded in the audit log. `SNUG_VIEWER_SESSION_DAYS` sets how long a viewer stays verified (7 days by default). The reader arrives in step 4.
- Email through any SMTP provider, configured with `SNUG_COMPANY_NAME`, `SNUG_EMAIL_FROM` and the `SNUG_SMTP_*` settings. `docs/email.md` covers the provider, the SPF, DKIM and DMARC records, and DigitalOcean's blocked SMTP ports. On `localhost`, emails are written to an outbox folder instead.

- A Documents section in the admin interface:
  - a list of documents with their latest version;
  - uploading a PDF as a new document;
  - a document page showing every version and a server-rendered preview of page 1;
  - uploading new versions.

  Oversized uploads are refused from their declared size, before they are read. Uploads, new versions and refusals are recorded in the audit log.

- Documents and versions, and the machinery behind them:
  - PDFs are checked, hashed and stored under generated names, never their uploaded names;
  - each upload becomes the document's next version;
  - PDFs are read and rendered in a separate worker process, with no access to Snug's settings, with limits on memory, CPU time, output size and open files, and a timeout. The worker is handed an open file rather than a path, and on Linux confines itself with Landlock, so it can open no files except fonts and make no network connections;
  - Snug's own processes are non-dumpable, so other processes of the same user cannot read their memory or environment;
  - rendered pages are cached as WebP, and the cache can be deleted at any time;
  - `SNUG_MAX_UPLOAD_MB` sets the largest upload (100 MB by default).
- `THIRD_PARTY_LICENCES.md`, listing the bundled PDFium components, fonts and icons.

- Signing in to the admin interface:
  - email and password, then a code from the authenticator app or a recovery code;
  - rate limits per address and per account;
  - idle and absolute session limits;
  - every sign-in, failure and sign-out recorded in the audit log.
- The front end from DESIGN.md revision 2, in production: the stylesheet, Geist fonts, the theme toggle (dark by default), the logo and the sign-in pages.
- `SNUG_TRUSTED_PROXIES`, so the client address is taken from `X-Forwarded-For` only when the request comes from Caddy.
- Admin accounts:
  - `snug createadmin`, with argon2id passwords, authenticator enrolment by QR code, and ten single-use recovery codes;
  - `snug reset-2fa` for an admin who has lost their phone (`docs/admin-accounts.md`).
- An append-only audit log, enforced by the database as well as the code. It records admin creation and second-factor resets.
- The Django project skeleton:
  - configuration from environment variables, checked at start-up with every problem listed at once (`docs/configuration.md`);
  - SQLite in WAL mode;
  - strict security headers on every response, and no CORS;
  - a `/healthz` check;
  - `snug serve`, which applies migrations, then starts gunicorn.

- Repository guard rails: CI for lint, type checking, tests on Python 3.12 and 3.14, secret scanning, dependency audit, licence checks and a software bill of materials; CodeQL; Dependabot; pre-commit hooks.
- Project documents: README, contributing guide, security policy, code of conduct, trademarks note, and agent instructions.
- DESIGN.md, recording the architecture, the agreed decisions and the front-end design, with clickable mockups and the Snug logo in `docs/design/`.
- The original project brief, kept for the record in `docs/BRIEF.md`.

[Unreleased]: https://github.com/Negroni-Venture-Studios/snug/commits/main
