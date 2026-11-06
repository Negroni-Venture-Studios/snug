# Changelog

All notable changes to Snug are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Snug uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Security

- The PDF worker refuses to read a file unless it has confined itself: Landlock ABI 4 (Linux 6.7) or later, and a new seccomp filter that refuses to create any socket. It used to carry on unconfined if Landlock failed, and Landlock alone let it send UDP. Both were found by an external security audit. `SNUG_UNSAFE_UNCONFINED_RENDERING` allows unconfined reading for development only.

### Fixed

- Settings, the new-link form and the room page kept the shared admin script out when adding their own, so their tabs did not scroll to the current one on a narrow screen.
- The README and guides describe Snug as it is: downloads are on by default, UK-only mode is still to come, and local emails can be read with one command. The design record lists the specified features not yet built. The container image now includes `THIRD_PARTY_LICENCES.md`, and pypdf is listed in it.
- Downloading a PDF that opens without a password but is encrypted against copying (common for published PDFs) failed with "could not be prepared for download". It is now stamped from an unencrypted copy. The confined worker also preloads every text codec it may need, since a PDF with UTF-16 metadata failed the same way.
- Links from a Snug on your own computer now work: `SNUG_DOMAIN` can be `localhost` with a port, like `localhost:8000`, and every link and email includes it. The README's trial command uses it. A real domain still cannot name a port.
- The admin and sign-in footers link to Negroni Venture Studios, and to the source on GitHub with GitHub's mark beside the repository name.
- Investors' pages show the company's name beside the logo, rather than the logo alone.
- Inviting an admin is a row at the foot of the admins list, the full width of the list, rather than a separate panel.
- The reader's page picker leaves room for its arrow, which sat on top of the page number, and is the same height as the buttons beside it.
- The company's name is set in Settings under Branding, beside the colour. It was only in the server's settings, as `SNUG_COMPANY_NAME`, which is now used only until a name is saved.
- The accent colour shows as a band across the top of every investor page, and on the reader's Download button. Once a document was open, it had been on almost nothing.
- Signing in, a wrong code from the authenticator app now says that codes change every 30 seconds, and taking more than five minutes after the password says so on the password page.
- Settings lists the admins first, then inviting one, then your account. Its buttons are as wide as their labels, Replace authenticator is orange like the others, and a code box beside other fields is the same height as they are.
- The theme toggle's sun, moon and half circle are Phosphor icons, like every other icon in Snug.
- A refused save in Settings opens where it was made, with its red error beside the field and in view, instead of at the top of the page. Inviting, removing or resetting an admin returns to its section too.
- Every save in the admin interface now says it worked, in a green notice in the section where it was made, and the page stays there instead of jumping to the top. A room's page says beside its name when it asks for the NDA, and its four tools sit two by two.
- Link expiry dates are shown as the last day the link works. The links list and the link email showed the day after.
- The README's instructions for trying Snug locally now keep the secret key in a file, and say how to update the container and set a forgotten password.
- The server no longer stalls, and then answers "Internal Server Error", when a browser holds connections open without using them. `snug serve` now runs threaded workers (`--threads`, 4 by default).
- An unexpected error is now logged with its traceback. Link and setup tokens in any log line are replaced with `[redacted]`.

### Added

- Your logo on investors' pages, uploaded in Settings under Branding: at the top of every page, on a light tile in dark mode, and as the icon in their browser tab. PNG or JPEG, up to 2 MB. Snug redraws it in the same confined process that reads PDFs, so nothing in the file but the picture reaches investors.
- Your accent colour on investors' pages, set in Settings under Branding with a picker and a live preview. Snug deepens or lightens the shade it uses for text and buttons where it needs to, so everything stays readable in light and dark mode, and says when it has.
- Uploads show their progress as a bar, then "Checking the PDF" while Snug reads it, and the button cannot be pressed twice.
- A cover message on a link: an optional note of up to 1,000 characters, shown to the investor in the About this link panel and in the email with the link.
- Replace your own authenticator in Settings, under Your account, for a new phone: your password and a code from the old app, or a recovery code. You go straight to setting up the new one, with new recovery codes, and the link is emailed in case you close the tab.
- Change your own password in Settings, under Your account, with your current password and a code from your authenticator app. You stay signed in on that browser, everywhere else is signed out, and you are emailed.
- With no NDA yet, ticking "Ask for the NDA" on a new link, or in a room's NDA panel, shows a box to write it there. Saving publishes it as version 1, as Settings would, instead of sending you away to Settings and losing what you typed.
- The People page shows how many times each person downloaded each document, and their total.
- An eye at the right of every password box shows or hides what is typed, for phones and fiddly keyboards. Password boxes also turn off autocorrect and capitalisation.
- "Forgotten your password?" on the sign-in page. It emails a link that works once, for one hour; setting the new password also needs a code from the authenticator app or a recovery code. The reply never says whether an address belongs to an admin. Without email, `snug reset-password EMAIL` does the same on the server, and is written to the audit log; Django's unaudited `changepassword` is disabled.
- The Overview: how many documents, active links, people who have read and visits this week, then the ten latest visits, with a person's first open of a document marked New.
- A container image, `ghcr.io/negroni-venture-studios/snug`, for amd64 and arm64: `:edge` from `main`, and a version tag and `:latest` from each release. It runs as an unprivileged user, and its render worker is confined by Landlock.

- Reading data is deleted automatically 12 months after its link is revoked or expires, a period you can change in Settings (from 1 to 120 months). `snug housekeeping`, which the server runs every hour, does it, and also clears expired codes, sessions, setup links and rate-limit counts.

- An Audit log tab: every admin and viewer event, newest first, filtered by kind or by email address, 100 to a page, with a CSV export of the same selection. Cells a spreadsheet would run as a formula are defused, and every export is itself recorded.
- The NDA: one agreement for the instance, written in Settings and versioned. A room, or a link to a single document, can ask for it before anything is shown. The viewer gives their name and ticks to accept; Snug records the name, email, link, time, IP address, browser and a fingerprint of the exact text, and asks again when the agreement changes. `docs/nda.md` says plainly that this is a record of acceptance, not a signature.

- Rooms, in the admin interface: create and rename a room, add folders (one level), and add, move and remove documents. A document can be in any number of rooms. Every change is recorded in the audit log.
- Sharing a room: one link opens a page listing the room's documents under their folders, each opening in the reader with its own downloads and reading time. Removing a document from the room stops the link reaching it at once.
- A People page: everyone who has opened a document, most recent first, with what each of them read in the same page strips.

- How a document was read, on its page: viewers, visits, median reading time and how many read every page, then one grid with the average time per page above a strip for each viewer, shaded by time on each page, with their visits, total time and when they were last seen.

- Reading time: each page a viewer is shown, and how long it was in view, measured by Snug from heartbeats sent only while the tab is visible. Visits end after 30 minutes away. Each new visit is recorded in the audit log. Nothing is sent anywhere outside the instance.
- An optional link to your own privacy policy, set in Settings and shown to viewers beside the statement of what Snug records about them.

- The reader: once a viewer is in, each page is shown as an image, one at a time, with Previous, Next, the arrow keys and a page picker. Every page is watermarked for that viewer on each request, with their email address and the date across the page and a footer naming them, the date and the link. Watermarks are stamped in the same confined worker that renders pages.
- Downloads: when a link allows them (the default), the reader offers the original PDF, marked on every page with the reader's faint diagonal watermark (the viewer's email address and the date) and a line naming them, the date and the link, with the same details in the file's properties. The text stays selectable and searchable. Each download is recorded in the audit log with a fingerprint of the exact file sent.

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
